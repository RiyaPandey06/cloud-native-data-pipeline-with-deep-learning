from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import func, select

from ..config import Settings
from ..db import initialise_database, make_session_factory
from ..models import (
    HOURLY_PIPELINE,
    TRAIN_PIPELINE,
    ModelRegistry,
    Observation,
    PipelineRun,
    QualityResult,
)
from ..repository import recent_predictions, run_summary

settings = Settings.from_env()
initialise_database(settings.database_url)
sessions = make_session_factory(settings.database_url)
app = FastAPI(title="Air Quality Forecast API", version="1.0.0")


def _active_model(session):
    return session.scalar(
        select(ModelRegistry)
        .where(ModelRegistry.is_active.is_(True))
        .order_by(ModelRegistry.created_at.desc())
        .limit(1)
    )


@app.get("/health")
def health() -> dict:
    """Liveness plus the three things a recruiter asks first: last good run, freshness, model."""
    with sessions() as session:
        last_success = session.scalar(
            select(PipelineRun)
            .where(PipelineRun.pipeline_name == HOURLY_PIPELINE, PipelineRun.status == "success")
            .order_by(PipelineRun.ended_at.desc())
            .limit(1)
        )
        last_run = session.scalar(
            select(PipelineRun)
            .where(PipelineRun.pipeline_name == HOURLY_PIPELINE)
            .order_by(PipelineRun.started_at.desc())
            .limit(1)
        )
        latest_observation = session.scalar(
            select(func.max(Observation.ts_utc)).where(
                Observation.location_id == settings.location_id
            )
        )
        active_model = _active_model(session)
        last_run_status = last_run.status if last_run else None
    if latest_observation is not None and latest_observation.tzinfo is None:
        latest_observation = latest_observation.replace(tzinfo=UTC)
    freshness_hours = (
        None
        if latest_observation is None
        else (datetime.now(UTC) - latest_observation).total_seconds() / 3600
    )
    limit = settings.quality.freshness_max_hours
    if last_success is None:
        status = "degraded"
    elif freshness_hours is not None and freshness_hours > limit:
        status = "stale"
    elif last_run_status in {"failed", "quarantined"}:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "last_successful_run": last_success.ended_at if last_success else None,
        "last_run_status": last_run_status,
        "latest_observation": latest_observation,
        "freshness_hours": freshness_hours,
        "freshness_limit_hours": limit,
        "active_model_version": active_model.model_version if active_model else None,
        "active_model_architecture": active_model.architecture if active_model else None,
    }


@app.get("/predict")
def predict(location_id: str | None = None, horizon: int = Query(default=24, ge=1, le=168)) -> dict:
    location = location_id or settings.location_id
    with sessions() as session:
        active_model = _active_model(session)
        predictions = recent_predictions(
            session,
            location,
            horizon,
            active_model.model_version if active_model else None,
        )
    if not predictions:
        raise HTTPException(
            status_code=404,
            detail="No scored forecast exists yet. Run ingestion and training first.",
        )
    return {
        "location_id": location,
        "model_version": predictions[0].model_version,
        "issued_at": predictions[0].issued_at,
        "forecast": [
            {"target_ts": row.target_ts, "pm25": row.forecast_pm25, "actual_pm25": row.actual_pm25}
            for row in predictions
        ],
    }


@app.get("/metrics")
def metrics() -> dict:
    """Pipeline counters. The success rate covers hourly runs only (training is reported apart)."""
    with sessions() as session:
        hourly = run_summary(session, HOURLY_PIPELINE)
        training = run_summary(session, TRAIN_PIPELINE)
        last_checks = session.scalars(
            select(QualityResult).order_by(QualityResult.id.desc()).limit(10)
        ).all()
        blocked_batches = (
            session.scalar(
                select(func.count(func.distinct(QualityResult.run_id))).where(
                    QualityResult.status.in_(["fail", "quarantine"])
                )
            )
            or 0
        )
    return {
        "pipeline_runs": hourly["runs"],
        "successful_runs": hourly["successful"],
        "failed_runs": hourly["failed"],
        "quarantined_runs": hourly["quarantined"],
        "success_rate": hourly["success_rate"],
        "bad_batches_caught": blocked_batches,
        "avg_rows_per_run": hourly["avg_rows_in"],
        "avg_run_seconds": hourly["avg_duration_seconds"],
        "first_run": hourly["first_run"],
        "last_run": hourly["last_run"],
        "training_runs": training["runs"],
        "training_success_rate": training["success_rate"],
        "recent_quality_checks": [
            {"name": item.check_name, "status": item.status, "detail": item.detail}
            for item in last_checks
        ],
    }


@app.get("/models")
def models(limit: int = Query(default=5, ge=1, le=20)) -> dict:
    """Registered models with their evaluation, minus the bulky curves."""
    with sessions() as session:
        records = session.scalars(
            select(ModelRegistry).order_by(ModelRegistry.created_at.desc()).limit(limit)
        ).all()
    return {
        "models": [
            {
                "model_version": record.model_version,
                "architecture": record.architecture,
                "created_at": record.created_at,
                "is_active": record.is_active,
                "metrics": {
                    key: value
                    for key, value in record.metrics.items()
                    if key not in {"loss_curve", "val_loss_curve"}
                },
            }
            for record in records
        ]
    }
