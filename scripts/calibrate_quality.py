"""Suggest data-quality thresholds from the history you have collected.

Run it after about two weeks of ingestion, then copy the printed values into your environment
(.env or Cloud Run variables). The thresholds in the PRD are proposals; this makes them data-led.
"""

from __future__ import annotations

import math

import pandas as pd

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import HOURLY_PIPELINE
from air_quality_pipeline.repository import observations_for_location, run_summary


def main() -> None:
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    sessions = make_session_factory(settings.database_url)
    with sessions() as session:
        rows = list(observations_for_location(session, settings.location_id))
        summary = run_summary(session, HOURLY_PIPELINE)
    if not rows:
        raise SystemExit("No observations yet; run the pipeline first.")
    frame = pd.DataFrame([{"ts_utc": r.ts_utc, "pm25": r.pm25, "pm10": r.pm10} for r in rows])
    frame = frame.sort_values("ts_utc")
    days = (frame.ts_utc.max() - frame.ts_utc.min()).total_seconds() / 86_400
    print(f"Calibrating on {len(frame)} hourly rows spanning {days:.1f} days")
    if days < 14:
        print("WARNING: under 14 days of history; treat these values as provisional.\n")

    def ceiling(series: pd.Series, floor: float) -> int:
        # 1.5x the largest value seen, rounded up to the next 10, never below a sane floor.
        return int(max(math.ceil(float(series.max()) * 1.5 / 10) * 10, floor))

    gaps = frame.ts_utc.diff().dropna().dt.total_seconds() / 3600
    print(f"observed PM2.5: max={frame.pm25.max():.1f}  p99={frame.pm25.quantile(0.99):.1f}")
    print(f"observed PM10 : max={frame.pm10.max():.1f}  p99={frame.pm10.quantile(0.99):.1f}")
    print(f"largest hourly gap in stored data: {gaps.max() if len(gaps) else 0:.0f} h")
    if summary["runs"]:
        print(f"average rows per run: {summary['avg_rows_in']:.0f}")
    print("\n# Suggested settings (add to .env):")
    print(f"PM25_CEILING={ceiling(frame.pm25, 150)}")
    print(f"PM10_CEILING={ceiling(frame.pm10, 250)}")
    print("GAP_WARN_HOURS=2   # keep unless the source regularly skips hours")
    print("GAP_FAIL_HOURS=6")


if __name__ == "__main__":
    main()
