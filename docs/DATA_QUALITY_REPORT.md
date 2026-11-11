# Data Quality Report

Generated 2026-11-11 18:01 UTC by `scripts/quality_report.py` from 1 hourly runs (2026-11-11 to 2026-11-11).

## Run outcomes

| Runs | Success | Failed | Quarantined | Success rate |
|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0 | 100.00% |

## Checks and thresholds in force

| Check | Pass | Warn | Quarantine | Fail | Rule |
|---|---:|---:|---:|---:|---|
| schema | 1 | 0 | 0 | 0 | required columns present and numeric |
| key_nulls | 1 | 0 | 0 | 0 | null share <= 1% |
| range | 1 | 0 | 0 | 0 | PM2.5 in [0, 1000], PM10 in [0, 1500] |
| duplicates | 1 | 0 | 0 | 0 | no duplicate timestamps |
| continuity | 1 | 0 | 0 | 0 | gap warn > 2 h, fail > 6 h |
| freshness | 1 | 0 | 0 | 0 | newest row within 2 h |
| volume | 1 | 0 | 0 | 0 | 80%-110% of expected rows |

## Bad batches caught (latest 20)

None recorded.

Thresholds are proposals; recalibrate with `scripts/calibrate_quality.py` once about two weeks of data exist, and note any change here.
