"""Leakage-free forecast evaluation: baselines, per-horizon metrics, rolling-origin folds.

This module is deliberately free of PyTorch and the database so the evaluation logic can be
unit-tested on its own. The neural networks are injected as ``NeuralFit`` callables (see
``training.py``).

Leakage rules enforced here:
* Rows are split chronologically; nothing is shuffled between train, validation and test.
* A window belongs to a split by where its *target* falls, so no training target overlaps a
  later split.
* Scalers are fitted only on rows that precede the evaluated block.
* Rolling-origin folds are expanding windows: each fold trains on everything before its block.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler

LOOKBACK = 24
HORIZON = 24
TRAIN_FRACTION = 0.70
VALID_FRACTION = 0.85
BASELINES = ("persistence", "seasonal_naive", "gradient_boosting")


@dataclass
class NeuralResult:
    """What a neural-network fit returns. Predictions are in scaled space."""

    val_pred: np.ndarray
    test_pred: np.ndarray
    train_losses: list[float]
    val_losses: list[float]
    artifact: object = None  # e.g. a state_dict; opaque to this module


NeuralFit = Callable[
    [np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray], NeuralResult
]  # (x_train, y_train, x_val, y_val, x_test) -> NeuralResult, all scaled


@dataclass(frozen=True)
class Fold:
    index: int
    block_start: int
    block_end: int
    train: np.ndarray
    test: np.ndarray


@dataclass
class EvaluationResult:
    models: list[str]
    metrics_by_model: dict[str, dict]
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    split_train: int
    split_valid: int
    test_windows: int
    artifacts: dict[str, object] = field(default_factory=dict)

    def metrics_for(self, architecture: str) -> dict:
        """The JSON stored in model_registry for one architecture."""
        return self.metrics_by_model[architecture]


# --------------------------------------------------------------------------- windows & splits


def make_windows(
    values: np.ndarray, lookback: int = LOOKBACK, horizon: int = HORIZON
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return inputs (N, lookback, F), raw PM2.5 targets (N, horizon) and target start rows."""
    starts = np.arange(lookback, len(values) - horizon + 1)
    if len(starts) == 0:
        empty_x = np.empty((0, lookback, values.shape[1]), dtype=np.float32)
        return empty_x, np.empty((0, horizon), dtype=np.float32), starts
    x = np.stack([values[s - lookback : s] for s in starts])
    y = np.stack([values[s : s + horizon, 0] for s in starts])
    return x, y, starts


def split_rows(
    n_rows: int, train_fraction: float = TRAIN_FRACTION, valid_fraction: float = VALID_FRACTION
) -> tuple[int, int]:
    return int(n_rows * train_fraction), int(n_rows * valid_fraction)


def holdout_masks(
    starts: np.ndarray, horizon: int, split_train: int, split_valid: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ends = starts + horizon
    train = ends <= split_train
    valid = (starts >= split_train) & (ends <= split_valid)
    test = starts >= split_valid
    return train, valid, test


def rolling_origin_folds(
    starts: np.ndarray,
    horizon: int,
    n_rows: int,
    first_origin_fraction: float = TRAIN_FRACTION,
    n_folds: int = 4,
    min_train_windows: int = 48,
) -> list[Fold]:
    """Expanding-window folds over the last (1 - first_origin_fraction) of the series."""
    edges = np.linspace(int(n_rows * first_origin_fraction), n_rows, n_folds + 1).astype(int)
    ends = starts + horizon
    folds = []
    for k in range(n_folds):
        low, high = int(edges[k]), int(edges[k + 1])
        train = ends <= low
        test = (starts >= low) & (starts < high)
        if train.sum() >= min_train_windows and test.any():
            folds.append(Fold(k, low, high, train, test))
    return folds


# --------------------------------------------------------------------------- metrics


def overall_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
    }


def per_horizon_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, list[float]]:
    """Error at each forecast hour (index 0 = one hour ahead)."""
    error = predicted - actual
    return {
        "mae": np.mean(np.abs(error), axis=0).tolist(),
        "rmse": np.sqrt(np.mean(error**2, axis=0)).tolist(),
    }


# --------------------------------------------------------------------------- core


def _scale_inputs(x: np.ndarray, mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return ((x - mean) / scale).astype(np.float32)


def _scale_target(y: np.ndarray, mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return ((y - mean[0]) / scale[0]).astype(np.float32)


def _fit_scaler(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scaler = StandardScaler().fit(rows)
    scale = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)  # constant columns stay finite
    return scaler.mean_.astype(np.float32), scale.astype(np.float32)


def _predict_baselines(
    x_train: np.ndarray,
    y_train_scaled: np.ndarray,
    x_test: np.ndarray,
    mean: np.ndarray,
    scale: np.ndarray,
    seed: int,
) -> dict[str, np.ndarray]:
    """Baselines in original PM2.5 units. They only see inputs available at issue time."""
    horizon = y_train_scaled.shape[1]
    x_train_scaled = _scale_inputs(x_train, mean, scale)
    x_test_scaled = _scale_inputs(x_test, mean, scale)
    boosted = MultiOutputRegressor(
        HistGradientBoostingRegressor(max_iter=150, random_state=seed)
    ).fit(x_train_scaled.reshape(len(x_train_scaled), -1), y_train_scaled)
    gradient = boosted.predict(x_test_scaled.reshape(len(x_test_scaled), -1))
    return {
        "persistence": np.repeat(x_test[:, -1, 0:1], horizon, axis=1),
        "seasonal_naive": x_test[:, -horizon:, 0],  # same hour on the previous day
        "gradient_boosting": gradient * scale[0] + mean[0],
    }


def _split_validation(train_mask: np.ndarray, fraction: float = 0.15) -> tuple[np.ndarray, ...]:
    """Carve the newest slice of a training mask off as validation for early stopping."""
    indices = np.flatnonzero(train_mask)
    n_valid = max(int(len(indices) * fraction), 1)
    return indices[:-n_valid], indices[-n_valid:]


def _run_models(
    x: np.ndarray,
    y: np.ndarray,
    train_idx: np.ndarray,
    valid_idx: np.ndarray,
    test_idx: np.ndarray,
    mean: np.ndarray,
    scale: np.ndarray,
    neural_fits: dict[str, NeuralFit],
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, NeuralResult]]:
    y_train_scaled = _scale_target(y[train_idx], mean, scale)
    predictions = _predict_baselines(x[train_idx], y_train_scaled, x[test_idx], mean, scale, seed)
    results: dict[str, NeuralResult] = {}
    for name, fit in neural_fits.items():
        results[name] = fit(
            _scale_inputs(x[train_idx], mean, scale),
            y_train_scaled,
            _scale_inputs(x[valid_idx], mean, scale),
            _scale_target(y[valid_idx], mean, scale),
            _scale_inputs(x[test_idx], mean, scale),
        )
        predictions[name] = results[name].test_pred * scale[0] + mean[0]
    return predictions, results


def run_evaluation(
    values: np.ndarray,
    neural_fits: dict[str, NeuralFit],
    n_folds: int = 4,
    seed: int = 42,
) -> EvaluationResult:
    """Evaluate baselines and neural fits on a chronological hold-out and rolling origins.

    ``values`` is (rows, features) in time order with PM2.5 in column 0.
    """
    values = np.asarray(values, dtype=np.float32)
    x, y, starts = make_windows(values)
    split_train, split_valid = split_rows(len(values))
    train_mask, valid_mask, test_mask = holdout_masks(starts, HORIZON, split_train, split_valid)
    if not train_mask.any() or not valid_mask.any() or not test_mask.any():
        raise ValueError("Chronological split produced an empty train, validation or test set")

    mean, scale = _fit_scaler(values[:split_train])  # training rows only
    train_idx, valid_idx, test_idx = (
        np.flatnonzero(train_mask),
        np.flatnonzero(valid_mask),
        np.flatnonzero(test_mask),
    )
    predictions, fitted = _run_models(
        x, y, train_idx, valid_idx, test_idx, mean, scale, neural_fits, seed
    )
    actual = y[test_idx]
    models = [*BASELINES, *neural_fits]
    holdout = {name: overall_metrics(actual, predictions[name]) for name in models}
    horizon_metrics = {name: per_horizon_metrics(actual, predictions[name]) for name in models}
    for name, result in fitted.items():
        val_actual = y[valid_idx]
        val_pred = result.val_pred * scale[0] + mean[0]
        holdout[name]["val_mae"] = overall_metrics(val_actual, val_pred)["mae"]

    # Rolling-origin: refit everything on an expanding window for each later block.
    per_fold: dict[str, list[dict[str, float]]] = {name: [] for name in models}
    folds = rolling_origin_folds(starts, HORIZON, len(values), n_folds=n_folds)
    for fold in folds:
        fold_train_idx, fold_valid_idx = _split_validation(fold.train)
        fold_test_idx = np.flatnonzero(fold.test)
        fold_mean, fold_scale = _fit_scaler(values[: fold.block_start])
        fold_predictions, _ = _run_models(
            x,
            y,
            fold_train_idx,
            fold_valid_idx,
            fold_test_idx,
            fold_mean,
            fold_scale,
            neural_fits,
            seed,
        )
        for name in models:
            per_fold[name].append(overall_metrics(y[fold_test_idx], fold_predictions[name]))
    rolling = {name: _summarise_folds(scores) for name, scores in per_fold.items() if scores}

    winner = min(models, key=lambda name: holdout[name]["mae"])
    metrics_by_model: dict[str, dict] = {}
    for name in neural_fits:
        baseline_mae = holdout["seasonal_naive"]["mae"]
        metrics_by_model[name] = {
            **{baseline: holdout[baseline] for baseline in BASELINES},
            name: holdout[name],
            "test_windows": int(len(test_idx)),
            "loss_curve": fitted[name].train_losses,
            "val_loss_curve": fitted[name].val_losses,
            "per_horizon": {model: horizon_metrics[model] for model in (*BASELINES, name)},
            "rolling_origin": {
                "folds": len(folds),
                "models": {
                    model: rolling[model] for model in (*BASELINES, name) if model in rolling
                },
            },
            "summary": {
                "winner": winner,
                "mae_change_vs_seasonal_naive_pct": (
                    (holdout[name]["mae"] - baseline_mae) / baseline_mae * 100
                    if baseline_mae
                    else None
                ),
            },
        }
    return EvaluationResult(
        models=models,
        metrics_by_model=metrics_by_model,
        scaler_mean=mean,
        scaler_scale=scale,
        split_train=split_train,
        split_valid=split_valid,
        test_windows=int(len(test_idx)),
        artifacts={name: result.artifact for name, result in fitted.items()},
    )


def _summarise_folds(scores: list[dict[str, float]]) -> dict:
    mae = np.array([score["mae"] for score in scores])
    rmse = np.array([score["rmse"] for score in scores])
    return {
        "mae_mean": float(mae.mean()),
        "mae_std": float(mae.std()),
        "rmse_mean": float(rmse.mean()),
        "rmse_std": float(rmse.std()),
        "mae_per_fold": mae.tolist(),
    }
