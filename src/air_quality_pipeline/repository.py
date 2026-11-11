from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import FeatureHourly, Observation, PipelineRun, Prediction, RawApiResponse

CHUNK_SIZE = 500  # keeps multi-row INSERTs well under driver bind-parameter limits


def _upsert(session: Session, table: Any, rows: list[dict], index_elements: list[str]) -> int:
    if not rows:
        return 0
    dialect = session.bind.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        # The development target is PostgreSQL; this fallback supports simple test databases.
        for row in rows:
            existing = session.execute(
                select(table).filter_by(**{key: row[key] for key in index_elements})
            ).scalar_one_or_none()
            if existing:
                for key, value in row.items():
                    setattr(existing, key, value)
            else:
                session.add(table(**row))
        return len(rows)
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    for offset in range(0, len(rows), CHUNK_SIZE):
        statement = insert(table).values(rows[offset : offset + CHUNK_SIZE])
        updates = {
            column.name: statement.excluded[column.name]
            for column in table.__table__.columns
            if column.name not in {"id", *index_elements}
        }
        session.execute(
            statement.on_conflict_do_update(index_elements=index_elements, set_=updates)
        )
    return len(rows)


def upsert_observations(session: Session, rows: list[dict]) -> int:
    return _upsert(session, Observation, rows, ["location_id", "ts_utc", "source"])


def upsert_features(session: Session, rows: list[dict]) -> int:
    return _upsert(session, FeatureHourly, rows, ["location_id", "ts_utc"])


def upsert_predictions(session: Session, rows: list[dict]) -> int:
    return _upsert(
        session, Prediction, rows, ["model_version", "location_id", "issued_at", "target_ts"]
    )


def reconcile_prediction_actuals(
    session: Session, location_id: str, observation_rows: list[dict]
) -> int:
    """Attach observed PM2.5 to previously issued forecasts once the target time arrives."""
    actual_by_timestamp = {
        _timestamp_key(row["ts_utc"]): float(row["pm25"]) for row in observation_rows
    }
    predictions = session.scalars(
        select(Prediction).where(Prediction.location_id == location_id)
    ).all()
    updated = 0
    for prediction in predictions:
        actual = actual_by_timestamp.get(_timestamp_key(prediction.target_ts))
        if actual is not None and prediction.actual_pm25 != actual:
            prediction.actual_pm25 = actual
            updated += 1
    return updated


def observations_for_location(session: Session, location_id: str) -> Iterable[Observation]:
    return session.scalars(
        select(Observation)
        .where(Observation.location_id == location_id)
        .order_by(Observation.ts_utc)
    ).all()


def recent_predictions(
    session: Session, location_id: str, horizon: int, model_version: str | None = None
) -> list[Prediction]:
    filters = [Prediction.location_id == location_id]
    if model_version is not None:
        filters.append(Prediction.model_version == model_version)
    latest_issue = select(Prediction.issued_at).where(*filters)
    latest_issue = latest_issue.order_by(Prediction.issued_at.desc()).limit(1)
    issue = session.scalar(latest_issue)
    if issue is None:
        return []
    return session.scalars(
        select(Prediction)
        .where(*filters, Prediction.issued_at == issue)
        .order_by(Prediction.target_ts)
        .limit(horizon)
    ).all()


def _timestamp_key(value):
    """Normalize SQLite's naive and PostgreSQL's UTC-aware values for equality joins."""
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value


def latest_observation_ts(session: Session, location_id: str):
    """Newest stored observation timestamp for a location, or None before the first load."""
    return session.scalar(
        select(func.max(Observation.ts_utc)).where(Observation.location_id == location_id)
    )


def purge_old_raw(session: Session, retention_days: int) -> int:
    """Delete raw payloads older than the retention window; returns rows removed."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = session.execute(
        delete(RawApiResponse)
        .where(RawApiResponse.fetched_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def run_summary(session: Session, pipeline_name: str, limit: int = 500) -> dict:
    """Success rate, volume and duration over the latest runs of one pipeline."""
    runs = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == pipeline_name, PipelineRun.status != "running")
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
    ).all()
    finished = [run for run in runs if run.ended_at is not None]
    durations = [
        (_aware(run.ended_at) - _aware(run.started_at)).total_seconds() for run in finished
    ]
    successes = sum(run.status == "success" for run in runs)
    return {
        "runs": len(runs),
        "successful": successes,
        "failed": sum(run.status == "failed" for run in runs),
        "quarantined": sum(run.status == "quarantined" for run in runs),
        "success_rate": successes / len(runs) if runs else None,
        "avg_rows_in": (sum(run.rows_in or 0 for run in runs) / len(runs)) if runs else None,
        "avg_duration_seconds": (sum(durations) / len(durations)) if durations else None,
        "first_run": min((_aware(run.started_at) for run in runs), default=None),
        "last_run": max((_aware(run.started_at) for run in runs), default=None),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
