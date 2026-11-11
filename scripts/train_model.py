from __future__ import annotations

import argparse
import json

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.retrain import run_training
from air_quality_pipeline.training import ARCHITECTURES


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train, evaluate (hold-out + rolling origin) and register forecasting models."
    )
    parser.add_argument("--architecture", choices=[*ARCHITECTURES, "all"], default="all")
    parser.add_argument("--epochs", type=int, default=30)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    architectures = ARCHITECTURES if args.architecture == "all" else (args.architecture,)
    outcomes = run_training(
        settings, make_session_factory(settings.database_url), architectures, args.epochs
    )
    for item in outcomes:
        summary = item.metrics["summary"]
        marker = " (active)" if item.active else ""
        print(
            f"{item.version}{marker}: hold-out MAE {item.metrics[item.architecture]['mae']:.3f}, "
            f"winner={summary['winner']}, "
            f"change vs seasonal naive {summary['mae_change_vs_seasonal_naive_pct']:+.1f}%"
        )
    print(json.dumps({"versions": [item.version for item in outcomes]}))


if __name__ == "__main__":
    main()
