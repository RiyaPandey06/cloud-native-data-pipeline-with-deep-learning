from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from .alerts import notify_failure
from .config import Settings
from .models import TRAIN_PIPELINE, PipelineRun
from .scoring import score_active_model
from .training import ARCHITECTURES, TrainOutcome, load_training_matrix, train_models

LOGGER = logging.getLogger(__name__)
TRAIN_PIPELINE_NAME = TRAIN_PIPELINE


def run_training(
    settings: Settings,
    sessions: sessionmaker[Session],
    architectures: tuple[str, ...] = ARCHITECTURES,
    epochs: int = 30,
) -> list[TrainOutcome]:
    """Retrain, register, activate the best model and score it, recording the run.

    Raises InsufficientDataError (without recording or alerting) while history is still short.
    """
    data = load_training_matrix(settings, sessions)
    run_id = str(uuid.uuid4())
    with sessions() as session:
        session.add(
            PipelineRun(
                run_id=run_id,
                pipeline_name=TRAIN_PIPELINE_NAME,
                started_at=datetime.now(UTC),
                status="running",
                rows_in=len(data[1]),
            )
        )
        session.commit()
    try:
        outcomes = train_models(settings, sessions, architectures, epochs, data=data)
        scored = score_active_model(settings, sessions)
    except Exception as exc:
        LOGGER.exception("training run %s failed", run_id)
        with sessions() as session:
            run = session.get(PipelineRun, run_id)
            run.status, run.ended_at = "failed", datetime.now(UTC)
            run.details = {"error": str(exc)}
            session.commit()
        notify_failure(settings.failure_webhook_url, f"Air-quality model retraining failed: {exc}")
        raise
    with sessions() as session:
        run = session.get(PipelineRun, run_id)
        run.status, run.ended_at = "success", datetime.now(UTC)
        run.rows_out = len(outcomes)
        run.details = {
            "versions": [item.version for item in outcomes],
            "active_version": next(item.version for item in outcomes if item.active),
            "forecast_horizons_scored": scored,
        }
        session.commit()
    return outcomes
