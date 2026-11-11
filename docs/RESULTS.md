# Model Results

Generated 2026-11-11 17:57 UTC from the model registry by `scripts/make_results.py`. The target is CAMS model output, not sensor readings.

Training rows: 1506 hourly observations (2026-09-10 onward). Hold-out windows: 203. Rolling-origin folds: 4.

## Hold-out (chronological 70/15/15) and rolling origin

| Model | Hold-out MAE | Hold-out RMSE | Rolling MAE (mean ± std) |
|---|---:|---:|---:|
| persistence | 7.188 | 8.982 | 8.458 ± 1.383 |
| seasonal_naive | 7.497 | 9.822 | 8.153 ± 1.510 |
| gradient_boosting | 7.180 | 9.589 | 7.268 ± 0.512 |
| cnn | 9.459 | 11.364 | 9.510 ± 0.822 |
| lstm | 8.097 | 10.232 | 8.301 ± 1.580 |

## Verdict

- **cnn** changed hold-out MAE by +26.2% versus seasonal naive; best hold-out model: `gradient_boosting`.
- **lstm** changed hold-out MAE by +8.0% versus seasonal naive; best hold-out model: `gradient_boosting`.

## Error by forecast hour (hold-out MAE)

| Model | +1h | +6h | +12h | +18h | +24h |
|---|---:|---:|---:|---:|---:|
| persistence | 1.96 | 6.56 | 8.32 | 8.59 | 7.46 |
| seasonal_naive | 7.59 | 7.50 | 7.54 | 7.49 | 7.46 |
| gradient_boosting | 2.38 | 6.26 | 7.04 | 9.03 | 9.34 |
| cnn | 6.93 | 8.33 | 9.66 | 10.51 | 11.89 |
| lstm | 5.38 | 6.25 | 8.01 | 9.32 | 10.52 |

If a baseline wins, that is the result: the pipeline, not the model, is the headline.
