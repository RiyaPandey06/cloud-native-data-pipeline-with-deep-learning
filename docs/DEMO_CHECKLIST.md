# Portfolio Evidence Checklist

Tick items only with real captured output. Nothing here is pre-filled.

| # | Evidence | How to produce it | Done |
|---|---|---|---|
| 1 | Live dashboard URL (green status, freshness, forecast vs actual, quality, attribution) | Deploy via `docs/DEPLOYMENT.md` | [ ] |
| 2 | `/docs` and `/health` showing last run and active model | Live API URL | [ ] |
| 3 | ≥ 4 weeks of runs and the measured success rate | `python scripts/evidence.py success-rate` and a `pipeline_runs` screenshot | [ ] |
| 4 | Idempotency: re-running a window adds zero duplicates | `python scripts/evidence.py idempotency` | [ ] |
| 5 | A bad batch rejected by the quality gate | `python scripts/evidence.py bad-batch` (screenshot), plus the alert in your chat tool | [ ] |
| 6 | Results table with baselines and both networks, rolling-origin and per-horizon | `python scripts/make_results.py` | [ ] |
| 7 | CI run (lint, format, tests, image build, secret scan) and a push-to-deploy run with its time | GitHub Actions tabs | [ ] |
| 8 | Airflow DAG graph from a local run | `docker-compose.airflow.yml` | [ ] |
| 9 | Data-quality report and model card | `python scripts/quality_report.py` writes `docs/DATA_QUALITY_REPORT.md`; model card is `docs/MODEL_CARD.md` | [ ] |
| 10 | Open-Meteo non-commercial answer recorded | `docs/DATA_TERMS.md` | [ ] |
| 11 | 2-minute demo: dashboard → API → tables → failed-quality example → metrics → Actions | Screen recording | [ ] |

Resume wording once measured (replace N, W, X with real values):

> Built and deployed a containerized hourly data pipeline (Python, PostgreSQL, Docker, Cloud Run, GitHub Actions CI/CD) with idempotent upserts and a quality gate; **N%** run success over **W** weeks. Trained PyTorch LSTM/CNN forecasters and compared them with seasonal-naive and gradient-boosting baselines using rolling-origin evaluation (**X%** MAE change versus seasonal naive); served via FastAPI.
