"""Reproduce the portfolio evidence the PRD asks for.

    python scripts/evidence.py idempotency   # re-run one window; zero duplicates
    python scripts/evidence.py bad-batch     # corrupted batch is quarantined and alerted
    python scripts/evidence.py success-rate  # run success rate from the real pipeline_runs

The first two use synthetic data in a throw-away SQLite file, never your real database, so
they work offline. Their output is for screenshots of the *mechanism*; it says nothing about
the real data. ``success-rate`` reads DATABASE_URL.
"""

from __future__ import annotations

import argparse
import math
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import (
    HOURLY_PIPELINE,
    Observation,
    PipelineRun,
    QualityResult,
    RawApiResponse,
)
from air_quality_pipeline.pipeline import Pipeline
from air_quality_pipeline.quality import DataQualityError
from air_quality_pipeline.repository import run_summary


class SyntheticClient:
    """Stands in for Open-Meteo: a clean daily PM2.5 cycle for the requested window."""

    def __init__(self, corrupt: bool = False):
        self.corrupt = corrupt

    def fetch(self, latitude: float, longitude: float, start: date, end: date) -> dict:
        first = datetime(start.year, start.month, start.day, tzinfo=UTC)
        last = min(
            datetime(end.year, end.month, end.day, 23, tzinfo=UTC),
            datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
        )
        hours = int((last - first).total_seconds() // 3600) + 1
        times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
        pm25 = [30 + 10 * math.sin(2 * math.pi * i / 24) for i in range(hours)]
        if self.corrupt:
            pm25[hours // 2] = -42.0  # an impossible reading
        air = {
            "hourly": {
                "time": times,
                "pm2_5": pm25,
                "pm10": [value * 1.6 for value in pm25],
                "nitrogen_dioxide": [8.0] * hours,
                "ozone": [45.0] * hours,
            }
        }
        weather = {
            "hourly": {
                "time": times,
                "temperature_2m": [24.0] * hours,
                "relative_humidity_2m": [60.0] * hours,
                "precipitation": [0.0] * hours,
                "wind_speed_10m": [3.0] * hours,
            }
        }
        return {
            "air_quality": ("synthetic://air", {"latitude": latitude}, air),
            "weather": ("synthetic://weather", {"longitude": longitude}, weather),
        }


def scratch_pipeline(corrupt: bool = False):
    folder = Path(tempfile.mkdtemp(prefix="evidence-"))
    url = f"sqlite:///{folder / 'evidence.db'}"
    settings = Settings(
        database_url=url,
        location_id="bengaluru",
        latitude=12.9716,
        longitude=77.5946,
        source="open-meteo-cams",
        backfill_days=3,
        schedule_minutes=60,
        artifact_dir=folder / "artifacts",
    )
    initialise_database(url)
    sessions = make_session_factory(url)
    return settings, sessions, Pipeline(settings, sessions, SyntheticClient(corrupt))


def idempotency() -> None:
    _, sessions, pipeline = scratch_pipeline()
    end = datetime.now(UTC).date()
    start = end - timedelta(days=2)
    print(f"Loading window {start} .. {end} twice (synthetic data)\n")

    def counts() -> tuple[int, int]:
        with sessions() as session:
            total = session.scalar(select(func.count()).select_from(Observation))
            distinct = session.scalar(
                select(func.count()).select_from(
                    select(Observation.location_id, Observation.ts_utc, Observation.source)
                    .distinct()
                    .subquery()
                )
            )
        return total, distinct

    pipeline.run(start, end)
    first_total, first_distinct = counts()
    print(f"after run 1: {first_total} observation rows ({first_distinct} distinct keys)")
    pipeline.run(start, end)
    second_total, second_distinct = counts()
    print(f"after run 2: {second_total} observation rows ({second_distinct} distinct keys)")
    duplicates = second_total - second_distinct
    print(f"\nrows added by the re-run: {second_total - first_total}")
    print(f"duplicate keys: {duplicates}")
    verdict = "PASS - re-running a window creates no duplicates" if not duplicates else "FAIL"
    print("RESULT:", verdict)


def bad_batch() -> None:
    settings, sessions, pipeline = scratch_pipeline(corrupt=True)
    end = datetime.now(UTC).date()
    print("Ingesting a batch with an impossible PM2.5 reading (-42) (synthetic data)\n")
    try:
        pipeline.run(end - timedelta(days=1), end)
    except DataQualityError as error:
        print(f"pipeline raised: {error}\n")
    with sessions() as session:
        run = session.scalar(select(PipelineRun))
        print(f"run status        : {run.status}")
        print(f"blocking checks   : {run.failed_checks}")
        raw_count = session.scalar(select(func.count()).select_from(RawApiResponse))
        clean_count = session.scalar(select(func.count()).select_from(Observation))
        print(f"raw payloads kept : {raw_count}")
        print(f"cleaned rows      : {clean_count}")
        print("\nquality_results:")
        checks = select(QualityResult).where(QualityResult.run_id == run.run_id)
        for row in session.scalars(checks):
            print(f"  {row.check_name:<11} {row.status:<10} {row.detail}")


def success_rate() -> None:
    settings = Settings.from_env()
    sessions = make_session_factory(settings.database_url)
    with sessions() as session:
        summary = run_summary(session, HOURLY_PIPELINE, limit=100_000)
    if not summary["runs"]:
        raise SystemExit("No hourly runs recorded yet.")
    span = summary["last_run"] - summary["first_run"]
    weeks = span.total_seconds() / (7 * 86_400)
    rate = summary["success_rate"]
    print(f"history      : {summary['first_run']:%Y-%m-%d} .. {summary['last_run']:%Y-%m-%d}")
    print(f"weeks        : {weeks:.1f}")
    print(
        f"runs         : {summary['runs']} ({summary['successful']} success, "
        f"{summary['failed']} failed, {summary['quarantined']} quarantined)"
    )
    print(f"success rate : {rate:.2%}  (target 95%)")
    if weeks < 4:
        print("NOTE: under four weeks of history; do not quote this as the final figure yet.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("proof", choices=["idempotency", "bad-batch", "success-rate"])
    args = parser.parse_args()
    {"idempotency": idempotency, "bad-batch": bad_batch, "success-rate": success_rate}[args.proof]()


if __name__ == "__main__":
    main()
