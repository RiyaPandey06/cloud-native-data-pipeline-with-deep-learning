# Model Card — PM2.5 Forecasting

## Purpose

Forecast the next 24 hourly PM2.5 values for one configured location from its recent air-quality and weather history. This is a portfolio demonstration of a reproducible data pipeline and leakage-aware model evaluation; it is not a public-health decision tool.

## Data

- Air quality: Open-Meteo Air Quality API, derived from CAMS ENSEMBLE model output.
- Weather covariates: Open-Meteo Weather API.
- Default location: Bengaluru, India (`12.9716, 77.5946`), configurable through environment variables.
- Storage: timestamps are UTC; raw source responses, cleaned observations and feature records are retained separately.

**Critical limitation:** the PM2.5 target is modelled output, not a ground-station measurement. Results quantify how well this project forecasts a source model's output, not air pollution measured at a sensor.

## Inputs and output

The LSTM and 1D CNN consume a 24-hour look-back window of PM2.5, PM10, NO2, O3, temperature, humidity, precipitation and wind speed. Each model produces a 24-hour PM2.5 forecast. The gradient-boosting baseline uses the same flattened windows. Calendar, lag and rolling-summary features are also stored in `features_hourly`.

## Evaluation protocol

- Chronological 70% / 15% / 15% train / validation / test split of rows; never shuffled between splits. A window belongs to a split by where its *target* falls.
- Normalisation parameters (and each rolling fold's) are fitted on rows before the evaluated block only.
- Validation drives early stopping (patience 5) and model selection; the test set is never used for either.
- Compare persistence, previous-day seasonal naive, gradient boosting, and each PyTorch architecture (LSTM, 1D CNN).
- Report hold-out MAE and RMSE, MAE/RMSE for each forecast hour (+1h to +24h), and rolling-origin results (expanding windows over the last 30% of the series, mean ± std across folds).
- Keep the baseline result even if it outperforms the networks. Stored in `model_registry.metrics`: hold-out scores, `per_horizon`, `rolling_origin`, loss curves, and a `summary` with the winner and MAE change versus seasonal naive.

## Responsible use

Do not use the forecast for medical, safety, regulatory or emergency decisions. Data availability, source-model revisions, missing values and location resolution can materially affect results. The quality gate prevents malformed, implausible, duplicate or badly discontinuous batches from reaching the cleaned and feature layers. Quality thresholds are proposals to be calibrated on real data.

## Reproduce or update

```bash
docker compose run --rm scheduler python scripts/run_pipeline.py
docker compose run --rm trainer python scripts/train_model.py --architecture all
```

The active model (best network by validation MAE) is recorded in `model_registry`; the serialized artifact is stored both under `artifacts/` and in `model_registry.artifact_blob`. A clean pipeline run scores the active model, and later ingestion joins arrived observations to prior forecasts for dashboard comparison.

## Attribution

Air-quality data is served by Open-Meteo and based on CAMS ENSEMBLE data. Use is subject to the provider's current terms; data is attributed under CC BY 4.0 as described in the project README and dashboard.
