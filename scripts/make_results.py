"""Write docs/RESULTS.md from the model registry (real numbers only, nothing typed by hand)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import ModelRegistry

MODELS = ["persistence", "seasonal_naive", "gradient_boosting"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/RESULTS.md"))
    args = parser.parse_args()
    settings = Settings.from_env()
    initialise_database(settings.database_url)
    sessions = make_session_factory(settings.database_url)
    with sessions() as session:
        records = session.scalars(
            select(ModelRegistry).order_by(ModelRegistry.created_at.desc())
        ).all()
    latest: dict[str, ModelRegistry] = {}
    for record in records:
        latest.setdefault(record.architecture, record)
    if not latest:
        raise SystemExit("No registered models yet; train one first.")

    reference = next(iter(latest.values()))
    rolling_folds = reference.metrics.get("rolling_origin", {}).get("folds", 0)
    lines = [
        "# Model Results",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from the model registry by "
        "`scripts/make_results.py`. The target is CAMS model output, not sensor readings.",
        "",
        f"Training rows: {reference.hyperparameters.get('training_rows', '?')} hourly "
        f"observations ({reference.train_start:%Y-%m-%d} onward). Hold-out windows: "
        f"{reference.metrics.get('test_windows', '?')}. Rolling-origin folds: {rolling_folds}.",
        "",
        "## Hold-out (chronological 70/15/15) and rolling origin",
        "",
        "| Model | Hold-out MAE | Hold-out RMSE | Rolling MAE (mean ± std) |",
        "|---|---:|---:|---:|",
    ]
    names = [*MODELS, *latest]
    for name in names:
        source = reference if name in MODELS else latest[name]
        score = source.metrics.get(name, {})
        rolling = source.metrics.get("rolling_origin", {}).get("models", {}).get(name, {})
        rolling_text = f"{rolling['mae_mean']:.3f} ± {rolling['mae_std']:.3f}" if rolling else "n/a"
        lines.append(
            f"| {name} | {score.get('mae', float('nan')):.3f} | "
            f"{score.get('rmse', float('nan')):.3f} | {rolling_text} |"
        )
    lines += ["", "## Verdict", ""]
    for architecture, record in latest.items():
        summary = record.metrics.get("summary", {})
        change = summary.get("mae_change_vs_seasonal_naive_pct")
        if change is not None:
            lines.append(
                f"- **{architecture}** changed hold-out MAE by {change:+.1f}% versus seasonal "
                f"naive; best hold-out model: `{summary.get('winner')}`."
            )
    lines += ["", "## Error by forecast hour (hold-out MAE)", ""]
    hours = (1, 6, 12, 18, 24)
    lines += [
        "| Model | " + " | ".join(f"+{hour}h" for hour in hours) + " |",
        "|---|" + "---:|" * len(hours),
    ]
    for name in names:
        source = reference if name in MODELS else latest[name]
        curve = source.metrics.get("per_horizon", {}).get(name, {}).get("mae")
        if curve:
            lines.append(
                f"| {name} | " + " | ".join(f"{curve[hour - 1]:.2f}" for hour in hours) + " |"
            )
    lines += [
        "",
        "If a baseline wins, that is the result: the pipeline, not the model, is the headline.",
        "",
    ]
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
