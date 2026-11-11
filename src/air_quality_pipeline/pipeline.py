from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .alerts import notify_failure
from .client import OpenMeteoClient
from .config import Settings
from .models import HOURLY_PIPELINE, PipelineRun, QualityResult, RawApiResponse
from .quality import (
    BLOCKING,
    CheckResult,
    DataQualityError,
    raise_if_failed,
    validate_observations,
    warnings_in,
)
from .repository import (
    latest_observation_ts,
    observations_for_location,
    purge_old_raw,
    reconcile_prediction_actuals,
    upsert_features,
    upsert_observations,
)
from .scoring import score_active_model
from .transform import (
    PreparedFrame,
    build_features,
    expected_row_count,
    prepare_frame,
    response_to_frame,
)

LOGGER = logging.getLogger(__name__)
PIPELINE_NAME = HOURLY_PIPELINE


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunContext:
    """Identifies one run so each stage can be a separate scheduler task (see airflow/dags)."""

    run_id: str
    start: date
    end: date


class Pipeline:
    """Hourly ingest -> validate -> load -> score.

    Each stage is a method that reads what the previous stage stored in the database, so the
    same code runs as one in-process call (:meth:`run`, used by the cron-style scheduler and the
    Cloud Run job) or as separate Airflow tasks.
    """

    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        client: OpenMeteoClient | None = None,
    ):
        self.settings = settings
        self.sessions = sessions
        self.client = client or OpenMeteoClient()

    # ------------------------------------------------------------------ orchestration

    def run(self, start: date | None = None, end: date | None = None) -> str:
        try:
            context = self.start_run(start, end)
        except Exception as exc:  # e.g. database unreachable: nothing could be recorded
            LOGGER.exception("pipeline run could not start")
            self._alert(f"Air-quality run could not start: {exc}")
            raise
        try:
            self.ingest(context)
            self.validate(context)
            self.load(context)
        except Exception as exc:
            LOGGER.exception("pipeline run %s failed", context.run_id)
            self.mark_failed(context.run_id, exc)
            raise
        self.score(context.run_id)
        return context.run_id

    # ------------------------------------------------------------------ stages

    def resolve_window(self, start: date | None, end: date | None) -> tuple[date, date, str]:
        """Pick the fetch window: explicit, a first-run backfill, or incremental with overlap."""
        end = end or utcnow().date()
        if start is not None:
            return start, end, "manual"
        with self.sessions() as session:
            latest = latest_observation_ts(session, self.settings.location_id)
        if latest is None:
            return end - timedelta(days=self.settings.backfill_days), end, "backfill"
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        # Re-fetching a short overlap picks up late arrivals and source revisions; upserts make
        # the repeated rows harmless.
        window_start = (latest - timedelta(hours=self.settings.overlap_hours)).date()
        return min(window_start, end), end, "incremental"

    def start_run(
        self,
        start: date | None = None,
        end: date | None = None,
        pipeline_name: str = PIPELINE_NAME,
    ) -> RunContext:
        window_start, window_end, mode = self.resolve_window(start, end)
        run_id = str(uuid.uuid4())
        with self.sessions() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    pipeline_name=pipeline_name,
                    started_at=utcnow(),
                    status="running",
                    details={
                        "mode": mode,
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                    },
                )
            )
            session.commit()
        return RunContext(run_id, window_start, window_end)

    def ingest(self, context: RunContext) -> None:
        """Fetch the window from the source and keep the untouched payloads (raw layer)."""
        responses = self.client.fetch(
            self.settings.latitude, self.settings.longitude, context.start, context.end
        )
        with self.sessions() as session:
            session.execute(delete(RawApiResponse).where(RawApiResponse.run_id == context.run_id))
            self._store_raw(session, context.run_id, responses)
            session.commit()

    def validate(self, context: RunContext) -> list[CheckResult]:
        """Run the quality gate on the raw payloads; blocking results stop the run."""
        now = utcnow()
        with self.sessions() as session:
            prepared = self._prepared_frame(session, context.run_id, now)
            checks = validate_observations(
                prepared.frame,
                pd.Timestamp(now),
                self.settings.quality,
                expected_rows=expected_row_count(context.start, context.end, now),
            )
            session.execute(delete(QualityResult).where(QualityResult.run_id == context.run_id))
            self._store_checks(session, context.run_id, checks)
            run = session.get(PipelineRun, context.run_id)
            run.details = {
                **(run.details or {}),
                "future_rows_dropped": prepared.future_rows_dropped,
                "late_rows_dropped": prepared.late_rows_dropped,
            }
            session.commit()
        warnings = warnings_in(checks)
        if warnings:
            self._alert(
                f"Air-quality run {context.run_id} loaded with warnings: "
                + "; ".join(f"{item.name}: {item.detail}" for item in warnings)
            )
        raise_if_failed(checks)
        return checks

    def load(self, context: RunContext) -> int:
        """Upsert the cleaned layer and rebuild features; mark the run successful."""
        now = utcnow()
        with self.sessions() as session:
            prepared = self._prepared_frame(session, context.run_id, now)
            frame = prepared.frame.dropna(subset=["ts_utc", "pm25"])
            rows_out = self._load_clean_and_features(session, frame)
            run = session.get(PipelineRun, context.run_id)
            run.status, run.ended_at = "success", utcnow()
            run.rows_in, run.rows_out = len(prepared.frame), rows_out
            purged = purge_old_raw(session, self.settings.raw_retention_days)
            run.details = {**(run.details or {}), "raw_rows_purged": purged}
            session.commit()
        LOGGER.info("pipeline run %s loaded %s rows", context.run_id, len(frame))
        return rows_out

    def score(self, run_id: str | None = None) -> int:
        """Emit forecasts from the active model. A scoring problem never undoes a clean load."""
        try:
            return score_active_model(self.settings, self.sessions)
        except Exception as exc:
            LOGGER.exception("scoring failed after a successful load")
            if run_id:
                with self.sessions() as session:
                    run = session.get(PipelineRun, run_id)
                    if run is not None:
                        run.details = {**(run.details or {}), "scoring_error": str(exc)}
                        session.commit()
            self._alert(f"Air-quality scoring failed (data loaded fine): {exc}")
            return 0

    def mark_failed(self, run_id: str, exc: BaseException) -> None:
        """Record a failed or quarantined run and alert. Safe to call as a scheduler callback."""
        quarantined = isinstance(exc, DataQualityError) and exc.quarantined
        status = "quarantined" if quarantined else "failed"
        with self.sessions() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return
            run.status, run.ended_at = status, utcnow()
            if isinstance(exc, DataQualityError):
                run.failed_checks = [r.name for r in exc.results if r.status in BLOCKING]
            run.details = {**(run.details or {}), "error": str(exc)}
            session.commit()
        self._alert(f"Air-quality run {run_id} {status}: {exc}")

    # ------------------------------------------------------------------ helpers

    def _alert(self, message: str) -> None:
        notify_failure(self.settings.failure_webhook_url, message)

    @staticmethod
    def _prepared_frame(session: Session, run_id: str, now: datetime) -> PreparedFrame:
        records = session.scalars(
            select(RawApiResponse).where(RawApiResponse.run_id == run_id)
        ).all()
        payloads = {record.endpoint: record.payload for record in records}
        if "air_quality" not in payloads or "weather" not in payloads:
            raise ValueError(f"Run {run_id} has no stored raw payloads; ingest must run first")
        frame = response_to_frame(payloads["air_quality"], payloads["weather"])
        return prepare_frame(frame, now)

    def _store_raw(self, session: Session, run_id: str, responses: dict) -> None:
        for endpoint, (url, params, payload) in responses.items():
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            record = RawApiResponse(
                id=str(uuid.uuid4()),
                run_id=run_id,
                endpoint=endpoint,
                location_id=self.settings.location_id,
                fetched_at=utcnow(),
                request_params={"url": url, **params},
                payload=payload,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            )
            session.add(record)

    @staticmethod
    def _store_checks(session: Session, run_id: str, checks: list) -> None:
        session.add_all(
            [
                QualityResult(
                    run_id=run_id,
                    check_name=check.name,
                    status=check.status,
                    detail=check.detail,
                    observed_value=check.observed_value,
                )
                for check in checks
            ]
        )

    def _load_clean_and_features(self, session: Session, frame: pd.DataFrame) -> int:
        now = utcnow()
        records = []
        for row in frame.to_dict("records"):
            records.append(
                {
                    "location_id": self.settings.location_id,
                    "ts_utc": row["ts_utc"].to_pydatetime(),
                    "source": self.settings.source,
                    "pm25": float(row["pm25"]),
                    "pm10": _float_or_none(row.get("pm10")),
                    "no2": _float_or_none(row.get("no2")),
                    "ozone": _float_or_none(row.get("ozone")),
                    "temperature_2m": _float_or_none(row.get("temperature_2m")),
                    "relative_humidity_2m": _float_or_none(row.get("relative_humidity_2m")),
                    "precipitation": _float_or_none(row.get("precipitation")),
                    "wind_speed_10m": _float_or_none(row.get("wind_speed_10m")),
                    "ingested_at": now,
                }
            )
        observation_count = upsert_observations(session, records)
        session.flush()
        reconcile_prediction_actuals(session, self.settings.location_id, records)
        all_rows = observations_for_location(session, self.settings.location_id)
        history = pd.DataFrame(
            [
                {
                    "ts_utc": row.ts_utc,
                    "pm25": row.pm25,
                    "pm10": row.pm10,
                    "no2": row.no2,
                    "ozone": row.ozone,
                    "temperature_2m": row.temperature_2m,
                    "relative_humidity_2m": row.relative_humidity_2m,
                    "precipitation": row.precipitation,
                    "wind_speed_10m": row.wind_speed_10m,
                }
                for row in all_rows
            ]
        )
        features = build_features(history)
        feature_rows = []
        for row in features.dropna().to_dict("records"):
            values = {
                key: _json_number(value)
                for key, value in row.items()
                if key not in {"ts_utc", "feature_built_at"}
            }
            feature_rows.append(
                {
                    "location_id": self.settings.location_id,
                    "ts_utc": row["ts_utc"].to_pydatetime()
                    if hasattr(row["ts_utc"], "to_pydatetime")
                    else row["ts_utc"],
                    "values": values,
                    "built_at": now,
                }
            )
        feature_count = upsert_features(session, feature_rows)
        return observation_count + feature_count


def _float_or_none(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _json_number(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
