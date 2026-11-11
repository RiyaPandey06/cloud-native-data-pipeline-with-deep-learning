from __future__ import annotations

import argparse
import logging
from datetime import date

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Open-Meteo data, validate it, and build features."
    )
    parser.add_argument("--start", type=date.fromisoformat, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="Inclusive YYYY-MM-DD")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    run_id = Pipeline(settings, make_session_factory(settings.database_url)).run(
        args.start, args.end
    )
    print(f"Pipeline run succeeded: {run_id}")


if __name__ == "__main__":
    main()
