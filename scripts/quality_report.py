"""Write docs/DATA_QUALITY_REPORT.md from the quality_results and pipeline_runs tables.

Everything in the report is computed from recorded runs; nothing is typed by hand.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import HOURLY_PIPELINE, PipelineRun, QualityResult
from air_quality_pipeline.repository import run_summary

CHECKS = ["schema", "key_nulls", "range", "duplicates", "continuity", "freshness", "volume"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/DATA_QUALITY_REPORT.md"))
    args = parser.parse_args()
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    sessions = make_session_factory(settings.database_url)
    with sessions() as session:
        summary = run_summary(session, HOURLY_PIPELINE, limit=100_000)
        results = session.scalars(select(QualityResult)).all()
        blocked = session.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.pipeline_name == HOURLY_PIPELINE,
                PipelineRun.status.in_(["failed", "quarantined"]),
            )
            .order_by(PipelineRun.started_at.desc())
            .limit(20)
        ).all()
    if not summary["runs"]:
        raise SystemExit("No hourly runs recorded yet.")

    by_check: dict[str, Counter] = defaultdict(Counter)
    for row in results:
        by_check[row.check_name][row.status] += 1

    quality = settings.quality
    lines = [
        "# Data Quality Report",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/quality_report.py` "
        f"from {summary['runs']} hourly runs "
        f"({summary['first_run']:%Y-%m-%d} to {summary['last_run']:%Y-%m-%d}).",
        "",
        "## Run outcomes",
        "",
        "| Runs | Success | Failed | Quarantined | Success rate |",
        "|---:|---:|---:|---:|---:|",
        f"| {summary['runs']} | {summary['successful']} | {summary['failed']} | "
        f"{summary['quarantined']} | {summary['success_rate']:.2%} |",
        "",
        "## Checks and thresholds in force",
        "",
        "| Check | Pass | Warn | Quarantine | Fail | Rule |",
        "|---|---:|---:|---:|---:|---|",
    ]
    rules = {
        "schema": "required columns present and numeric",
        "key_nulls": f"null share <= {quality.max_null_share:.0%}",
        "range": f"PM2.5 in [0, {quality.pm25_ceiling:g}], PM10 in [0, {quality.pm10_ceiling:g}]",
        "duplicates": "no duplicate timestamps",
        "continuity": (
            f"gap warn > {quality.gap_warn_hours:g} h, fail > {quality.gap_fail_hours:g} h"
        ),
        "freshness": f"newest row within {quality.freshness_max_hours:g} h",
        "volume": f"{quality.volume_min_share:.0%}-{quality.volume_max_share:.0%} of expected rows",
    }
    for name in CHECKS:
        counts = by_check.get(name, Counter())
        lines.append(
            f"| {name} | {counts['pass']} | {counts['warn']} | {counts['quarantine']} | "
            f"{counts['fail']} | {rules[name]} |"
        )
    lines += ["", "## Bad batches caught (latest 20)", ""]
    if blocked:
        lines += ["| Started (UTC) | Status | Blocking checks |", "|---|---|---|"]
        for run in blocked:
            lines.append(
                f"| {run.started_at:%Y-%m-%d %H:%M} | {run.status} | "
                f"{', '.join(run.failed_checks or []) or '(source error)'} |"
            )
    else:
        lines.append("None recorded.")
    lines += [
        "",
        "Thresholds are proposals; recalibrate with `scripts/calibrate_quality.py` once about "
        "two weeks of data exist, and note any change here.",
        "",
    ]
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
