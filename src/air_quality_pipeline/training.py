from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from matplotlib.figure import Figure
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .config import Settings
from .evaluation import HORIZON, LOOKBACK, NeuralFit, NeuralResult, run_evaluation
from .models import ModelRegistry
from .repository import observations_for_location

ARCHITECTURES = ("lstm", "cnn")
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
MIN_TRAINING_ROWS = 168  # one week of hourly data is the least that gives non-empty splits
EARLY_STOP_PATIENCE = 5


class InsufficientDataError(ValueError):
    """Not enough complete history yet; the retrain worker waits instead of alerting."""


class LSTMForecaster(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 32, horizon: int = HORIZON):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, horizon)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, (hidden, _) = self.lstm(x)
        return self.head(hidden[-1])


class CNNForecaster(nn.Module):
    """Second architecture: a compact temporal 1D CNN."""

    def __init__(self, input_size: int, horizon: int = HORIZON):
        super().__init__()
        self.network = nn.Sequential(
            nn.Conv1d(input_size, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Linear(32, horizon)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.network(x.transpose(1, 2)).squeeze(-1))


def build_model(architecture: str, input_size: int) -> nn.Module:
    if architecture == "lstm":
        return LSTMForecaster(input_size)
    if architecture == "cnn":
        return CNNForecaster(input_size)
    raise ValueError(f"Unknown architecture {architecture!r}; choose from {ARCHITECTURES}")


@dataclass
class TrainOutcome:
    version: str
    architecture: str
    metrics: dict
    artifact_path: Path
    training_rows: int = 0
    active: bool = False


def make_neural_fit(
    architecture: str, epochs: int = 30, patience: int = EARLY_STOP_PATIENCE, seed: int = 42
) -> NeuralFit:
    """Build the fit callable the evaluation harness uses for hold-out and every rolling fold.

    The validation slice drives early stopping; the best epoch's weights are restored. Inputs
    and targets arrive already scaled with training-only statistics.
    """

    def fit(
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_val: np.ndarray,
        y_val: np.ndarray,
        x_test: np.ndarray,
    ) -> NeuralResult:
        torch.manual_seed(seed)
        model = build_model(architecture, x_train.shape[-1])
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()
        loader = DataLoader(
            TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
            batch_size=32,
            shuffle=True,  # shuffling *within* the training windows is fine; splits stay ordered
            generator=torch.Generator().manual_seed(seed),
        )
        x_val_t, y_val_t = torch.from_numpy(x_val), torch.from_numpy(y_val)
        train_losses: list[float] = []
        val_losses: list[float] = []
        best_state = copy.deepcopy(model.state_dict())
        best_val, stale_epochs = float("inf"), 0
        for _ in range(epochs):
            model.train()
            batch_losses = []
            for batch_x, batch_y in loader:
                optimizer.zero_grad()
                loss = loss_fn(model(batch_x), batch_y)
                loss.backward()
                optimizer.step()
                batch_losses.append(float(loss.item()))
            train_losses.append(float(np.mean(batch_losses)))
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(x_val_t), y_val_t).item())
            val_losses.append(val_loss)
            if val_loss < best_val - 1e-6:
                best_val, best_state, stale_epochs = val_loss, copy.deepcopy(model.state_dict()), 0
            else:
                stale_epochs += 1
                if stale_epochs >= patience:
                    break
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            val_pred = model(x_val_t).numpy()
            test_pred = model(torch.from_numpy(x_test)).numpy()
        return NeuralResult(val_pred, test_pred, train_losses, val_losses, best_state)

    return fit


def load_training_matrix(
    settings: Settings, sessions: sessionmaker[Session]
) -> tuple[pd.DataFrame, np.ndarray]:
    with sessions() as session:
        observations = list(observations_for_location(session, settings.location_id))
    frame = pd.DataFrame(
        [
            {column: getattr(row, column) for column in ["ts_utc", *FEATURE_COLUMNS]}
            for row in observations
        ]
    )
    if frame.empty:
        raise InsufficientDataError("No observations stored yet; run the pipeline first")
    frame = frame.dropna().sort_values("ts_utc").reset_index(drop=True)
    if len(frame) < MIN_TRAINING_ROWS:
        raise InsufficientDataError(
            f"Need at least {MIN_TRAINING_ROWS} complete hourly observations to train; "
            f"found {len(frame)}"
        )
    return frame, frame[FEATURE_COLUMNS].to_numpy(dtype=np.float32)


def train_models(
    settings: Settings,
    sessions: sessionmaker[Session],
    architectures: tuple[str, ...] = ARCHITECTURES,
    epochs: int = 30,
    n_folds: int = 4,
    data: tuple[pd.DataFrame, np.ndarray] | None = None,
) -> list[TrainOutcome]:
    """Evaluate and register every architecture; the best by validation MAE becomes active.

    Baselines are fitted once and shared. The registry keeps one row per architecture with the
    full metric set (hold-out, per-horizon, rolling-origin), so a baseline beating the networks
    is visible in the results rather than hidden.
    """
    frame, values = data if data is not None else load_training_matrix(settings, sessions)
    fits = {name: make_neural_fit(name, epochs) for name in architectures}
    result = run_evaluation(values, fits, n_folds=n_folds)

    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    outcomes: list[TrainOutcome] = []
    for name in architectures:
        metrics = result.metrics_for(name)
        version = f"{name}-{stamp}-{uuid.uuid4().hex[:8]}"
        artifact_path = settings.artifact_dir / f"{version}.pt"
        torch.save(
            {
                "state_dict": result.artifacts[name],
                "architecture": name,
                "input_size": int(values.shape[1]),
                "scaler_mean": result.scaler_mean.tolist(),
                "scaler_scale": result.scaler_scale.tolist(),
                "feature_columns": FEATURE_COLUMNS,
            },
            artifact_path,
        )
        _save_loss_plot(settings.artifact_dir / f"{version}-loss.png", metrics)
        with sessions() as session:
            session.add(
                ModelRegistry(
                    model_version=version,
                    created_at=datetime.now(UTC),
                    train_start=frame.ts_utc.iloc[0].to_pydatetime(),
                    train_end=frame.ts_utc.iloc[result.split_train - 1].to_pydatetime(),
                    architecture=name,
                    hyperparameters={
                        "lookback": LOOKBACK,
                        "horizon": HORIZON,
                        "epochs": epochs,
                        "early_stop_patience": EARLY_STOP_PATIENCE,
                        "rolling_origin_folds": metrics["rolling_origin"]["folds"],
                        "training_rows": int(len(values)),
                    },
                    metrics=metrics,
                    artifact_path=str(artifact_path),
                    artifact_blob=artifact_path.read_bytes(),
                    is_active=False,
                )
            )
            session.commit()
        outcomes.append(TrainOutcome(version, name, metrics, artifact_path, len(values)))

    best = min(outcomes, key=lambda item: item.metrics[item.architecture]["val_mae"])
    activate_model(sessions, best.version)
    best.active = True
    return outcomes


def train(
    settings: Settings,
    sessions: sessionmaker[Session],
    architecture: str = "lstm",
    epochs: int = 30,
) -> TrainOutcome:
    """Train and activate a single architecture (kept for the one-model CLI path)."""
    return train_models(settings, sessions, (architecture,), epochs)[0]


def activate_model(sessions: sessionmaker[Session], version: str) -> None:
    with sessions() as session:
        session.execute(update(ModelRegistry).values(is_active=False))
        session.execute(
            update(ModelRegistry)
            .where(ModelRegistry.model_version == version)
            .values(is_active=True)
        )
        session.commit()


def _save_loss_plot(path: Path, metrics: dict) -> None:
    figure = Figure(figsize=(7, 3))
    axis = figure.subplots()
    axis.plot(metrics["loss_curve"], color="#1f77b4", label="train")
    axis.plot(metrics["val_loss_curve"], color="#d62728", label="validation")
    axis.set(title="Training loss", xlabel="Epoch", ylabel="MSE (scaled)")
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
