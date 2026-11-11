"""Run one pipeline stage from the command line (used by the Airflow DAG's tasks).

    python scripts/run_stage.py start
    python scripts/run_stage.py ingest   --context '{"run_id": "...", "start": "...", "end": "..."}'
    python scripts/run_stage.py validate --context '...'
    python scripts/run_stage.py load     --context '...'
    python scripts/run_stage.py score    --context '...'

``start`` prints the run context as JSON on its last line; the other stages read it back. A stage
records the run as failed (and alerts) only on its final attempt, so a retry that succeeds
leaves no false failure behind. Quality-gate rejections are final immediately: retrying the same
bad batch cannot help.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.pipeline import Pipeline, RunContext
from air_quality_pipeline.quality import DataQualityError

STAGES = ("start", "ingest", "validate", "load", "score")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--context", help="JSON printed by the start stage")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=1)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings.from_env()
    initialise_database(settings.database_url)
    pipeline = Pipeline(settings, make_session_factory(settings.database_url))

    if args.stage == "start":
        context = pipeline.start_run()
        print(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "start": context.start.isoformat(),
                    "end": context.end.isoformat(),
                }
            )
        )
        return 0

    if not args.context:
        parser.error("--context is required for this stage")
    raw = json.loads(args.context)
    context = RunContext(
        raw["run_id"], date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    )
    try:
        if args.stage == "ingest":
            pipeline.ingest(context)
        elif args.stage == "validate":
            pipeline.validate(context)
        elif args.stage == "load":
            pipeline.load(context)
        else:
            pipeline.score(context.run_id)
    except DataQualityError as error:
        logging.error("quality gate stopped the run: %s", error)
        pipeline.mark_failed(context.run_id, error)
        return 1
    except Exception as error:
        logging.exception(
            "stage %s failed (attempt %d/%d)", args.stage, args.attempt, args.max_attempts
        )
        if args.attempt >= args.max_attempts:
            pipeline.mark_failed(context.run_id, error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
