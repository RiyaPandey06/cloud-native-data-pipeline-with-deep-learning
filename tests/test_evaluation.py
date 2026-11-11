import numpy as np
from sklearn.linear_model import Ridge

from air_quality_pipeline.evaluation import (
    HORIZON,
    LOOKBACK,
    NeuralResult,
    holdout_masks,
    make_windows,
    per_horizon_metrics,
    rolling_origin_folds,
    run_evaluation,
    split_rows,
)


def synthetic_series(rows: int = 700, seed: int = 0) -> np.ndarray:
    """Daily cycle plus noise; column 0 is PM2.5, the rest are weather-like covariates."""
    rng = np.random.default_rng(seed)
    hours = np.arange(rows)
    pm25 = 30 + 10 * np.sin(2 * np.pi * hours / 24) + rng.normal(0, 2, rows)
    covariates = rng.normal(size=(rows, 3))
    return np.column_stack([pm25, covariates]).astype(np.float32)


def ridge_fit(x_train, y_train, x_val, y_val, x_test) -> NeuralResult:
    """Stand-in for a neural network: lets the evaluation harness be tested without PyTorch."""
    model = Ridge(alpha=1.0).fit(x_train.reshape(len(x_train), -1), y_train)

    def predict(x):
        return model.predict(x.reshape(len(x), -1)).astype(np.float32)

    return NeuralResult(predict(x_val), predict(x_test), [1.0, 0.5], [1.1, 0.6], model)


def test_windows_target_the_next_horizon_of_pm25():
    values = synthetic_series(100)
    x, y, starts = make_windows(values)
    assert x.shape == (len(starts), LOOKBACK, 4)
    assert y.shape == (len(starts), HORIZON)
    first = starts[0]
    np.testing.assert_array_equal(x[0], values[first - LOOKBACK : first])
    np.testing.assert_array_equal(y[0], values[first : first + HORIZON, 0])


def test_holdout_splits_never_overlap_in_time():
    values = synthetic_series(400)
    _, _, starts = make_windows(values)
    split_train, split_valid = split_rows(len(values))
    train, valid, test = holdout_masks(starts, HORIZON, split_train, split_valid)
    assert not (train & valid).any() and not (train & test).any() and not (valid & test).any()
    # no training target reaches into validation/test territory
    assert (starts[train] + HORIZON).max() <= split_train
    assert (starts[valid] + HORIZON).max() <= split_valid
    assert starts[test].min() >= split_valid


def test_rolling_folds_are_expanding_and_leak_free():
    values = synthetic_series(600)
    _, _, starts = make_windows(values)
    folds = rolling_origin_folds(starts, HORIZON, len(values), n_folds=4)
    assert len(folds) == 4
    previous_train = 0
    for fold in folds:
        assert (starts[fold.train] + HORIZON).max() <= fold.block_start
        assert starts[fold.test].min() >= fold.block_start
        assert fold.train.sum() > previous_train  # expanding window
        previous_train = fold.train.sum()


def test_per_horizon_metrics_have_one_value_per_hour():
    actual = np.zeros((5, HORIZON))
    predicted = np.ones((5, HORIZON))
    metrics = per_horizon_metrics(actual, predicted)
    assert metrics["mae"] == [1.0] * HORIZON and metrics["rmse"] == [1.0] * HORIZON


def test_run_evaluation_reports_baselines_per_horizon_and_rolling_origin():
    result = run_evaluation(synthetic_series(), {"ridge": ridge_fit}, n_folds=3)
    metrics = result.metrics_for("ridge")
    for model in ("persistence", "seasonal_naive", "gradient_boosting", "ridge"):
        assert metrics[model]["mae"] > 0
        assert len(metrics["per_horizon"][model]["mae"]) == HORIZON
        assert metrics["rolling_origin"]["models"][model]["mae_std"] >= 0
    assert metrics["rolling_origin"]["folds"] == 3
    assert metrics["ridge"]["val_mae"] > 0
    assert metrics["loss_curve"] == [1.0, 0.5] and metrics["val_loss_curve"] == [1.1, 0.6]
    assert metrics["summary"]["winner"] in metrics
    # On a clean daily cycle the learned model must clearly beat last-value persistence.
    assert metrics["ridge"]["mae"] < metrics["persistence"]["mae"]


def test_scaler_uses_training_rows_only():
    values = synthetic_series(500)
    values[int(500 * 0.70) :, 0] += 1_000  # huge shift after the training window
    result = run_evaluation(values, {"ridge": ridge_fit}, n_folds=2)
    assert result.scaler_mean[0] < 100  # the shift never leaked into the scaler


def test_too_little_data_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        run_evaluation(synthetic_series(60), {"ridge": ridge_fit})
