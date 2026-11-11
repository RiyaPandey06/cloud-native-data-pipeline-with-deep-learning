from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import Observation, PipelineRun, QualityResult, RawApiResponse
from air_quality_pipeline.pipeline import Pipeline
from air_quality_pipeline.quality import DataQualityError
from air_quality_pipeline.repository import purge_old_raw


class StubClient:
    def __init__(self, pm25=(11.0, 12.0, 13.0)):
        self.pm25 = list(pm25)

    def fetch(self, latitude: float, longitude: float, start: date, end: date) -> dict:
        times = ["2026-10-01T00:00", "2026-10-01T01:00", "2026-10-01T02:00"]
        air_payload = {
            "hourly": {
                "time": times,
                "pm2_5": self.pm25,
                "pm10": [20.0, 21.0, 22.0],
                "nitrogen_dioxide": [2.0, 3.0, 4.0],
                "ozone": [30.0, 31.0, 32.0],
            }
        }
        weather_payload = {
            "hourly": {
                "time": times,
                "temperature_2m": [25.0, 25.5, 26.0],
                "relative_humidity_2m": [50.0, 51.0, 52.0],
                "precipitation": [0.0, 0.0, 0.0],
                "wind_speed_10m": [4.0, 4.5, 5.0],
            }
        }
        return {
            "air_quality": ("https://example.test/air", {"latitude": latitude}, air_payload),
            "weather": ("https://example.test/weather", {"longitude": longitude}, weather_payload),
        }


def build(tmp_path, client=None, webhook=None):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    initialise_database(database_url)
    settings = Settings(
        database_url=database_url,
        location_id="bengaluru",
        latitude=12.9716,
        longitude=77.5946,
        source="open-meteo-cams",
        backfill_days=1,
        schedule_minutes=60,
        artifact_dir=tmp_path / "artifacts",
        failure_webhook_url=webhook,
    )
    sessions = make_session_factory(database_url)
    return settings, sessions, Pipeline(settings, sessions, client or StubClient())


def test_pipeline_records_runs_and_remains_idempotent(tmp_path):
    _, sessions, pipeline = build(tmp_path)
    pipeline.run(date(2026, 10, 1), date(2026, 10, 1))
    pipeline.run(date(2026, 10, 1), date(2026, 10, 1))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 3
        assert session.scalar(select(func.count()).select_from(RawApiResponse)) == 4
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 2
        assert session.scalar(select(PipelineRun.status).distinct()) == "success"


def test_first_run_backfills_then_later_runs_are_incremental(tmp_path):
    _, sessions, pipeline = build(tmp_path)
    pipeline.run()
    pipeline.run()
    with sessions() as session:
        runs = session.scalars(select(PipelineRun).order_by(PipelineRun.started_at)).all()
    assert [run.details["mode"] for run in runs] == ["backfill", "incremental"]
    # latest stored observation is 2026-10-01 02:00; the overlap (48 h) reaches back to 09-29
    assert runs[1].details["window_start"] == "2026-09-29"


def test_bad_batch_is_quarantined_alerted_and_never_reaches_cleaned_layer(tmp_path, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(
        "air_quality_pipeline.pipeline.notify_failure", lambda url, message: alerts.append(message)
    )
    _, sessions, pipeline = build(
        tmp_path, StubClient(pm25=(-5.0, 12.0, 13.0)), webhook="https://hooks.example/test"
    )
    with pytest.raises(DataQualityError):
        pipeline.run(date(2026, 10, 1), date(2026, 10, 1))
    with sessions() as session:
        run = session.scalar(select(PipelineRun))
        assert run.status == "quarantined"
        assert run.failed_checks == ["range"]
        assert session.scalar(select(func.count()).select_from(Observation)) == 0
        assert session.scalar(select(func.count()).select_from(RawApiResponse)) == 2
        range_check = session.scalar(
            select(QualityResult).where(QualityResult.check_name == "range")
        )
        assert range_check.status == "quarantine"
    assert any("quarantined" in message for message in alerts)


def test_warnings_load_the_batch_but_still_alert(tmp_path, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(
        "air_quality_pipeline.pipeline.notify_failure", lambda url, message: alerts.append(message)
    )
    _, sessions, pipeline = build(tmp_path, webhook="https://hooks.example/test")
    pipeline.run(date(2026, 10, 1), date(2026, 10, 1))  # 3 rows of ~24 expected, and stale
    with sessions() as session:
        assert session.scalar(select(PipelineRun.status)) == "success"
    assert any("warnings" in message and "volume" in message for message in alerts)


def test_old_raw_payloads_are_purged(tmp_path):
    _, sessions, pipeline = build(tmp_path)
    pipeline.run(date(2026, 10, 1), date(2026, 10, 1))
    with sessions() as session:
        for record in session.scalars(select(RawApiResponse)):
            record.fetched_at = record.fetched_at.replace(year=2020)
        session.commit()
        assert purge_old_raw(session, retention_days=30) == 2
        session.commit()
        assert session.scalar(select(func.count()).select_from(RawApiResponse)) == 0
