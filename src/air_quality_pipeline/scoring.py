from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .evaluation import LOOKBACK
from .models import ModelRegistry
from .repository import observations_for_location, upsert_predictions

FEATURE_COLUMNS = [
    "pm25",
    "pm10",
    "no2",
    "ozone",
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
]


def score_active_model(settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Score the active model when enough clean history exists; no-op before first training run."""
    with sessions() as session:
        model_record = session.scalar(
            select(ModelRegistry)
            .where(ModelRegistry.is_active.is_(True))
            .order_by(ModelRegistry.created_at.desc())
            .limit(1)
        )
        observations = list(observations_for_location(session, settings.location_id))
    if model_record is None:
        return 0
    frame = pd.DataFrame(
        [
            {column: getattr(row, column) for column in ["ts_utc", *FEATURE_COLUMNS]}
            for row in observations
        ]
    ).dropna()
    if len(frame) < LOOKBACK:
        return 0
    # Import the ML runtime only after we know an active model needs scoring. This keeps
    # ingestion usable independently while a model is still being built.
    import torch

    from .training import CNNForecaster, LSTMForecaster

    checkpoint_source = (
        BytesIO(model_record.artifact_blob)
        if model_record.artifact_blob is not None
        else model_record.artifact_path
    )
    checkpoint = torch.load(checkpoint_source, map_location="cpu", weights_only=True)
    input_size = int(checkpoint["input_size"])
    model = (
        LSTMForecaster(input_size)
        if checkpoint["architecture"] == "lstm"
        else CNNForecaster(input_size)
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    mean = np.asarray(checkpoint["scaler_mean"], dtype=np.float32)
    scale = np.asarray(checkpoint["scaler_scale"], dtype=np.float32)
    values = frame[FEATURE_COLUMNS].tail(LOOKBACK).to_numpy(dtype=np.float32)
    model_input = torch.tensor(((values - mean) / scale)[None, :, :])
    with torch.no_grad():
        forecast = model(model_input).numpy().ravel() * scale[0] + mean[0]
    issued_at = pd.Timestamp(frame.ts_utc.iloc[-1]).to_pydatetime()
    rows = [
        {
            "model_version": model_record.model_version,
            "location_id": settings.location_id,
            "issued_at": issued_at,
            "target_ts": issued_at + timedelta(hours=offset),
            "forecast_pm25": float(value),
            "actual_pm25": None,
        }
        for offset, value in enumerate(forecast, start=1)
    ]
    with sessions() as session:
        count = upsert_predictions(session, rows)
        session.commit()
    return count
