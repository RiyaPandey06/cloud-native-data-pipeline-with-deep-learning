from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from air_quality_pipeline.config import Settings
from air_quality_pipeline.db import initialise_database, make_session_factory
from air_quality_pipeline.models import (
    HOURLY_PIPELINE,
    ModelRegistry,
    Observation,
    PipelineRun,
    QualityResult,
)
from air_quality_pipeline.repository import recent_predictions, run_summary

BASELINES = ["persistence", "seasonal_naive", "gradient_boosting"]
STATUS_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "quarantine": "🚫"}

st.set_page_config(page_title="Air Quality Pipeline", page_icon="🌬️", layout="wide")
settings = Settings.from_env()
initialise_database(settings.database_url)
sessions = make_session_factory(settings.database_url)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


with sessions() as session:
    summary = run_summary(session, HOURLY_PIPELINE)
    recent_runs = session.scalars(
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == HOURLY_PIPELINE)
        .order_by(PipelineRun.started_at.desc())
        .limit(168)
    ).all()
    quality_rows = session.scalars(
        select(QualityResult).order_by(QualityResult.id.desc()).limit(2_000)
    ).all()
    models = session.scalars(
        select(ModelRegistry).order_by(ModelRegistry.created_at.desc()).limit(10)
    ).all()
    active = next((item for item in models if item.is_active), None)
    latest_ts = aware(
        session.scalar(
            select(Observation.ts_utc)
            .where(Observation.location_id == settings.location_id)
            .order_by(Observation.ts_utc.desc())
            .limit(1)
        )
    )
    forecasts = recent_predictions(
        session, settings.location_id, 24, active.model_version if active else None
    )

freshness_hours = (datetime.now(UTC) - latest_ts).total_seconds() / 3600 if latest_ts else None
limit = settings.quality.freshness_max_hours
last_run = recent_runs[0] if recent_runs else None

st.title("PM2.5 Forecasting Pipeline")
st.caption(
    f"Scheduled, idempotent pipeline for {settings.location_id.title()} using Open-Meteo / "
    "CAMS modelled air-quality data. The target is model output, not sensor readings."
)

# ------------------------------------------------------------------ pipeline health
if last_run is None:
    st.info("The pipeline has not run yet.")
elif last_run.status != "success":
    st.error(f"Last run {last_run.status}. See the quality checks below.")
elif freshness_hours is not None and freshness_hours > limit:
    st.warning(
        f"Data is stale: newest observation is {freshness_hours:.1f} h old (limit {limit:g} h)."
    )
else:
    st.success("Pipeline healthy: last run succeeded and data is fresh.")

columns = st.columns(6)
columns[0].metric("Last run", last_run.status if last_run else "Not run")
columns[1].metric(
    "Success rate",
    f"{summary['success_rate']:.1%}" if summary["success_rate"] is not None else "—",
    help=f"{summary['successful']} of {summary['runs']} hourly runs",
)
columns[2].metric("Freshness", f"{freshness_hours:.1f} h" if freshness_hours is not None else "—")
columns[3].metric(
    "Rows per run", f"{summary['avg_rows_in']:.0f}" if summary["avg_rows_in"] else "—"
)
columns[4].metric(
    "Run duration",
    f"{summary['avg_duration_seconds']:.1f} s" if summary["avg_duration_seconds"] else "—",
)
columns[5].metric(
    "Bad batches caught",
    summary["quarantined"] + summary["failed"],
    help="Runs stopped by the quality gate or a source failure",
)
if summary["first_run"]:
    st.caption(
        f"Run history since {summary['first_run']:%Y-%m-%d %H:%M} UTC "
        f"({summary['runs']} runs). The 95% target needs about four weeks of history."
    )

# ------------------------------------------------------------------ forecast vs actual
st.subheader("Forecast versus actual")
if forecasts:
    data = pd.DataFrame(
        [
            {"target": item.target_ts, "forecast": item.forecast_pm25, "actual": item.actual_pm25}
            for item in forecasts
        ]
    )
    figure = go.Figure()
    figure.add_scatter(x=data.target, y=data.forecast, mode="lines+markers", name="Forecast")
    figure.add_scatter(x=data.target, y=data.actual, mode="lines+markers", name="Actual")
    figure.update_layout(yaxis_title="PM2.5 μg/m³", margin={"t": 10})
    st.plotly_chart(figure, use_container_width=True)
    st.caption(f"Model {active.model_version if active else '—'}; actuals fill in as hours pass.")
else:
    st.info("No forecast yet: one appears after a model is trained and the next clean run.")

# ------------------------------------------------------------------ run history
st.subheader("Run history")
if recent_runs:
    history = pd.DataFrame(
        [
            {
                "started": aware(run.started_at),
                "status": run.status,
                "rows_in": run.rows_in,
                "seconds": (
                    (aware(run.ended_at) - aware(run.started_at)).total_seconds()
                    if run.ended_at
                    else None
                ),
                "mode": (run.details or {}).get("mode"),
                "failed_checks": ", ".join(run.failed_checks or []),
            }
            for run in recent_runs
        ]
    ).sort_values("started")
    left, right = st.columns(2)
    rows_chart = go.Figure(go.Bar(x=history.started, y=history.rows_in, name="Rows in"))
    rows_chart.update_layout(title="Rows per run", margin={"t": 40})
    left.plotly_chart(rows_chart, use_container_width=True)
    seconds_chart = go.Figure(
        go.Scatter(x=history.started, y=history.seconds, mode="lines+markers")
    )
    seconds_chart.update_layout(title="Run duration (s)", margin={"t": 40})
    right.plotly_chart(seconds_chart, use_container_width=True)
    st.dataframe(history.sort_values("started", ascending=False).head(20), use_container_width=True)
else:
    st.info("No runs recorded yet.")

# ------------------------------------------------------------------ data quality
st.subheader("Data quality")
if quality_rows:
    latest_run_id = quality_rows[0].run_id
    latest_checks = [row for row in quality_rows if row.run_id == latest_run_id]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "check": row.check_name,
                    "status": f"{STATUS_ICON.get(row.status, '')} {row.status}",
                    "detail": row.detail,
                }
                for row in reversed(latest_checks)
            ]
        ),
        use_container_width=True,
    )
    counts = pd.Series([row.status for row in quality_rows]).value_counts()
    st.caption(
        "Checks recorded across recent runs: "
        + ", ".join(f"{count} {status}" for status, count in counts.items())
    )
else:
    st.info("No quality results yet.")

# ------------------------------------------------------------------ model comparison
st.subheader("Model comparison")
if active:
    metrics = active.metrics
    architecture = active.architecture
    names = [*BASELINES, architecture]
    rolling = metrics.get("rolling_origin", {}).get("models", {})
    table = pd.DataFrame(
        [
            {
                "model": name,
                "hold-out MAE": metrics.get(name, {}).get("mae"),
                "hold-out RMSE": metrics.get(name, {}).get("rmse"),
                "rolling MAE (mean)": rolling.get(name, {}).get("mae_mean"),
                "rolling MAE (std)": rolling.get(name, {}).get("mae_std"),
            }
            for name in names
        ]
    )
    st.dataframe(table.round(3), use_container_width=True, hide_index=True)
    outcome = metrics.get("summary", {})
    change = outcome.get("mae_change_vs_seasonal_naive_pct")
    if outcome:
        st.write(
            f"**Best hold-out model: `{outcome.get('winner')}`.** The active {architecture} "
            f"changed MAE by {change:+.1f}% versus seasonal naive "
            f"({metrics.get('test_windows', '?')} hold-out windows, "
            f"{metrics.get('rolling_origin', {}).get('folds', 0)} rolling-origin folds). "
            "A baseline winning is a valid, reported result."
            if change is not None
            else f"Best hold-out model: `{outcome.get('winner')}`."
        )
    per_horizon = metrics.get("per_horizon")
    if per_horizon:
        horizon_chart = go.Figure()
        for name in names:
            if name in per_horizon:
                values = per_horizon[name]["mae"]
                horizon_chart.add_scatter(
                    x=list(range(1, len(values) + 1)), y=values, mode="lines", name=name
                )
        horizon_chart.update_layout(
            title="Error by forecast hour (hold-out MAE)",
            xaxis_title="Hours ahead",
            yaxis_title="MAE μg/m³",
        )
        st.plotly_chart(horizon_chart, use_container_width=True)
    if metrics.get("loss_curve"):
        loss_chart = go.Figure()
        loss_chart.add_scatter(y=metrics["loss_curve"], name="train")
        if metrics.get("val_loss_curve"):
            loss_chart.add_scatter(y=metrics["val_loss_curve"], name="validation")
        loss_chart.update_layout(title=f"{architecture} loss (scaled MSE)", xaxis_title="Epoch")
        st.plotly_chart(loss_chart, use_container_width=True)
    st.caption(f"Active version: {active.model_version}")
else:
    st.info("No model trained yet.")

st.divider()
st.caption(
    "Air-quality data: Open-Meteo Air Quality API, based on CAMS ENSEMBLE model output "
    "(Copernicus Atmosphere Monitoring Service). Weather data: Open-Meteo. Data is CC BY 4.0; "
    "this dashboard forecasts modelled output, not ground-station observations."
)
