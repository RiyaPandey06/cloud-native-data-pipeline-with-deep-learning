from __future__ import annotations

import logging
import time

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.retrain import run_training
from air_quality_pipeline.training import InsufficientDataError


def main() -> None:
    """Retrain every RETRAIN_INTERVAL_HOURS; wait quietly until enough history exists."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    sessions = make_session_factory(settings.database_url)
    while True:
        try:
            outcomes = run_training(settings, sessions)
            active = next(item for item in outcomes if item.active)
            logging.info("registered %s; active model is %s", len(outcomes), active.version)
            time.sleep(settings.retrain_interval_hours * 3600)
        except InsufficientDataError as exc:
            logging.info("not training yet: %s", exc)
            time.sleep(settings.train_retry_minutes * 60)
        except Exception:
            logging.exception("retraining failed (alert sent by run_training); retrying later")
            time.sleep(settings.train_retry_minutes * 60)


if __name__ == "__main__":
    main()
