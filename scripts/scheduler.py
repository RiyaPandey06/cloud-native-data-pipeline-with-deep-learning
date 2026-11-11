from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.pipeline import Pipeline
from air_quality_pipeline.schedule import seconds_until_next_slot


def main() -> None:
    """Cron-style worker: runs on clock-aligned slots (hh:05 for the default hourly schedule).

    Failures are recorded in ``pipeline_runs`` and alerted by the pipeline itself, so this loop
    only logs and carries on to the next slot; the incremental window re-covers a missed slot.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    pipeline = Pipeline(settings, make_session_factory(settings.database_url))
    while True:
        try:
            pipeline.run()
        except Exception:
            logging.exception("scheduled run failed; see pipeline_runs and quality_results")
        pause = seconds_until_next_slot(datetime.now(UTC), settings.schedule_minutes)
        logging.info("next run in %.0f seconds", pause)
        time.sleep(pause)


if __name__ == "__main__":
    main()
