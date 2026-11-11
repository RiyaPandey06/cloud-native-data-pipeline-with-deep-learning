# Cloud-Native PM2.5 Forecasting Pipeline

<p align="center">
  <b>An end-to-end, containerized data engineering and deep-learning project for hourly PM2.5 forecasting.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PyTorch-Deep%20Learning-ee4c2c?logo=pytorch" alt="PyTorch">
  <img src="https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-Database-4169e1?logo=postgresql" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ed?logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/Tests-37%20passed-success" alt="Tests">
</p>

A scheduled pipeline that ingests air-quality and weather data, validates and stores it in PostgreSQL, builds time-series features, trains and evaluates forecasting models, and serves forecasts through FastAPI and a Streamlit dashboard.

**The engineering focus is the complete, reproducible pipeline:** ingestion, data quality, idempotent storage, model evaluation, model registry, API serving, monitoring, and containerized orchestration.

> **Important data limitation:** The PM2.5 target comes from the CAMS ENSEMBLE atmospheric model through the Open-Meteo Air Quality API. It is **not ground-station sensor data**. The evaluation therefore measures forecasting performance against model-generated values, not measured pollution. This project is a technical demonstration, not a public-health or emergency decision system.

---

## Contents

- [Project overview](#project-overview)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Pipeline workflow](#pipeline-workflow)
- [Data and features](#data-and-features)
- [Model training and evaluation](#model-training-and-evaluation)
- [Results](#results)
- [Data quality and reliability](#data-quality-and-reliability)
- [Database design](#database-design)
- [API reference](#api-reference)
- [Run locally](#run-locally)
- [Testing and code quality](#testing-and-code-quality)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [Limitations and next steps](#limitations-and-next-steps)
- [Documentation](#documentation)
- [Data attribution](#data-attribution)

## Project overview

| Area | Implementation |
|---|---|
| Use case | Hourly PM2.5 time-series forecasting |
| Default location | Bengaluru, India |
| Data source | Open-Meteo Air Quality API and Weather API |
| Air-quality target | CAMS ENSEMBLE modelled PM2.5 |
| Ingestion cadence | Hourly |
| Initial backfill | 90 days |
| Incremental overlap | 48 hours, to capture late data and source revisions |
| Forecast horizon | Next 24 hours |
| Neural architectures | LSTM and 1D CNN |
| Baselines | Persistence, seasonal naive, Gradient Boosting |
| Database | PostgreSQL |
| API | FastAPI |
| Dashboard | Streamlit |
| Orchestration | Docker Compose; optional Airflow DAG |
| Deployment support | Google Cloud deployment scripts and an opt-in GitHub Actions workflow |

## Screenshots

### System architecture

![Cloud-native pipeline architecture](docs/architecture.png)

The architecture diagram shows the data source, scheduled ingestion, validation, PostgreSQL storage layers, training/scoring components, API, and dashboard.

### Application screenshots

The repository currently includes the architecture diagram. Actual runtime screenshots of the Streamlit dashboard, API responses, and container services have not yet been added. They should be captured from a real local run rather than represented with mock images.

Recommended screenshots to add under `docs/screenshots/`:

| Suggested filename | What to capture |
|---|---|
| `dashboard-overview.png` | Streamlit dashboard with pipeline status, latest forecast, and model information |
| `dashboard-forecast.png` | Forecast chart showing the next 24 hours |
| `api-health.png` | FastAPI `/health` response and active model |
| `api-predict.png` | FastAPI `/predict?horizon=6` response |
| `docker-services.png` | Docker Compose services running |
| `model-results.png` | Model comparison table or results report |

Once captured, embed them here with Markdown image links, for example:
`![Dashboard overview](docs/screenshots/dashboard-overview.png)`.

## Architecture

The pipeline is composed of independent stages so scheduled runs, manual backfills, and the optional Airflow DAG can reuse the same processing logic.

**Main flow**

1. Fetch hourly air-quality and weather data from Open-Meteo.
2. Preserve the raw response and its SHA-256 hash.
3. Validate schema, nulls, ranges, duplicates, continuity, freshness, and row volume.
4. Transform valid observations and upsert them into PostgreSQL.
5. Build lag, rolling-statistic, and calendar features.
6. Train and evaluate baseline and neural forecasting models.
7. Register model metadata and activate the best neural network by validation MAE.
8. Generate a 24-hour forecast and expose it through FastAPI.
9. Display forecasts and pipeline health in Streamlit.

## Technology stack

| Layer | Tools |
|---|---|
| Language | Python |
| Data ingestion | Open-Meteo APIs, HTTP client, retry/backoff |
| Data processing | pandas, NumPy |
| Machine learning | scikit-learn |
| Deep learning | PyTorch — LSTM, 1D CNN |
| Database | PostgreSQL, SQLAlchemy |
| API | FastAPI, Uvicorn |
| Dashboard | Streamlit |
| Scheduling and orchestration | Docker Compose scheduler, optional Apache Airflow |
| Containers | Docker, Docker Compose |
| Testing and quality | pytest, Ruff, pre-commit, Gitleaks |
| Cloud deployment support | Google Cloud Run, Cloud SQL, Cloud Scheduler, GitHub Actions OIDC |

## Pipeline workflow

### 1. Ingestion

- Retrieves air-quality and weather variables from Open-Meteo.
- Uses retries with exponential backoff and jitter; respects `Retry-After` when provided.
- Stores the original JSON response, request parameters, and SHA-256 hash.
- Performs an initial 90-day backfill.
- Subsequent runs fetch new data with a 48-hour overlap to account for delayed publication and source revisions.

### 2. Data validation

A quality gate runs before cleaned observations and features are written. Blocking failures prevent downstream writes; implausible batches are quarantined.

| Check | Current rule | Action |
|---|---|---|
| Schema | Required columns must exist and be numeric | Fail if invalid |
| Key nulls | Timestamp/PM2.5 null share must be at most 1% | Drop permitted null rows; warn/fail based on threshold |
| Range | PM2.5 in [0, 1000], PM10 in [0, 1500] | Quarantine implausible batch |
| Duplicates | No duplicate timestamps | Fail |
| Continuity | Gap over 2 hours warns; over 6 hours fails | Warn/fail |
| Freshness | Newest row within 2 scheduling intervals | Warn and alert |
| Volume | 80–110% of expected rows | Warn and alert |

Thresholds are configurable and should be calibrated against a longer period of observed pipeline behaviour.

### 3. Transformation and storage

- Removes future forecast timestamps and the newest hours that have not yet been published as observations.
- Converts source data into typed hourly records.
- Uses database upserts keyed by location, timestamp, and source.
- Rebuilds time-series features after loading.
- Keeps raw, cleaned, and feature data in separate logical layers.

### 4. Training and scoring

- Trains persistence, seasonal-naive, Gradient Boosting, LSTM, and 1D CNN candidates.
- Uses chronological splits and rolling-origin evaluation.
- Uses validation data for early stopping and neural model selection.
- Registers model versions and evaluation metadata.
- Scores the active registered neural model for the next 24 hours.
- Joins actual values to earlier forecasts as observations become available.

## Data and features

The neural models use a **24-hour look-back window** with these eight input variables:

| Feature | Description |
|---|---|
| PM2.5 | Fine particulate matter concentration |
| PM10 | Coarse particulate matter concentration |
| NO2 | Nitrogen dioxide |
| O3 | Ozone |
| Temperature | Air temperature |
| Humidity | Relative humidity |
| Precipitation | Precipitation |
| Wind speed | Wind speed |

**Input shape:** 24 time steps × 8 features  
**Forecast output:** 24 hourly PM2.5 values

The feature layer also stores lag features, rolling statistics, and calendar features. The default configured location is Bengaluru (latitude 12.9716, longitude 77.5946); it can be changed through environment configuration.

## Model training and evaluation

### Evaluation protocol

- Chronological **70% / 15% / 15%** train, validation, and test split.
- No random shuffling across time splits.
- A sequence is assigned to a split based on where its target falls.
- Scalers are fitted only on training data available before the evaluated block.
- Rolling-origin evaluation uses expanding training windows across four folds.
- Validation MAE is used for early stopping and neural-network selection.
- Test metrics are reported separately and are not used for model selection.

### Models compared

| Model | Type |
|---|---|
| Persistence | Baseline — latest value carried forward |
| Seasonal Naive | Baseline — previous-day value |
| Gradient Boosting | Machine-learning baseline |
| LSTM | Recurrent neural network |
| 1D CNN | Convolutional neural network for time series |

## Results

The following results are from the committed evaluation report, generated on **11 November 2026**. The evaluation used **1,506 hourly observations**, **203 hold-out windows**, and **4 rolling-origin folds**.

### Hold-out performance

Lower MAE and RMSE indicate lower prediction error.

| Model | Hold-out MAE | Hold-out RMSE | Rolling-origin MAE (mean ± std) |
|---|---:|---:|---:|
| Persistence | 7.188 | 8.982 | 8.458 ± 1.383 |
| Seasonal Naive | 7.497 | 9.822 | 8.153 ± 1.510 |
| **Gradient Boosting** | **7.180** | 9.589 | **7.268 ± 0.512** |
| CNN | 9.459 | 11.364 | 9.510 ± 0.822 |
| LSTM | 8.097 | 10.232 | 8.301 ± 1.580 |

**Best hold-out MAE: Gradient Boosting (7.180).** It also achieved the lowest rolling-origin MAE mean (7.268).

The neural models did not outperform the strongest baseline in this evaluation. The CNN hold-out MAE was 26.2% higher than seasonal naive, while LSTM was 8.0% higher. This is reported transparently rather than presenting the neural networks as the winning models.

### Hold-out MAE by forecast horizon

| Model | +1 hour | +6 hours | +12 hours | +18 hours | +24 hours |
|---|---:|---:|---:|---:|---:|
| Persistence | 1.96 | 6.56 | 8.32 | 8.59 | 7.46 |
| Seasonal Naive | 7.59 | 7.50 | 7.54 | 7.49 | 7.46 |
| Gradient Boosting | 2.38 | 6.26 | 7.04 | 9.03 | 9.34 |
| CNN | 6.93 | 8.33 | 9.66 | 10.51 | 11.89 |
| LSTM | 5.38 | 6.25 | 8.01 | 9.32 | 10.52 |

### Registered model

The latest recorded registry run selected the **LSTM** as the active neural model based on validation MAE:

| Registry entry | Value |
|---|---|
| Active architecture | LSTM |
| Model version | `lstm-20261111T174759Z-4e48ca5c` |
| Validation MAE | 8.09375 |
| CNN model version | `cnn-20261111T174759Z-a94a1a09` |
| CNN validation MAE | 8.63667 |

**Important distinction:** the active LSTM is the selected neural model in the registry; Gradient Boosting is the best model in the reported hold-out comparison. The API serves the active registered neural model, not necessarily the overall hold-out winner.

See [docs/RESULTS.md](docs/RESULTS.md) for the generated report and [docs/MODEL_CARD.md](docs/MODEL_CARD.md) for intended use and evaluation details.

## Data quality and reliability

The committed quality report was generated from **one recorded hourly run**:

| Runs | Successful | Failed | Quarantined | Observed success rate |
|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0 | 100% |

All seven recorded checks passed in that run: schema, key nulls, range, duplicates, continuity, freshness, and volume.

> This is a one-run observation, not evidence of long-term reliability. A meaningful operational success rate requires several weeks of scheduled runs. No bad batch was recorded in this report.

## Database design

| Layer | Table | Purpose |
|---|---|---|
| Raw | `raw_api_responses` | Original source payloads, request parameters, and hashes |
| Cleaned | `observations` | Typed hourly observations with a unique location/timestamp/source key |
| Features | `features_hourly` | Lag, rolling, and calendar features |
| Serving | `predictions` | Forecast values and timestamps |
| Registry | `model_registry` | Model versions, artifacts, and evaluation metrics |
| Operations | `pipeline_runs` | Run status, row counts, and duration |
| Operations | `quality_results` | Evidence and outcomes for individual quality checks |

Raw payload retention is configurable; the design currently specifies a 30-day retention period.

## API reference

Start the stack and open the interactive API documentation at **http://localhost:8000/docs**.

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Pipeline status, last successful run, data freshness, and active model |
| `/predict?horizon=24` | GET | Latest forecast for the requested horizon |
| `/metrics` | GET | Operational metrics for pipeline runs and quality outcomes |
| `/models` | GET | Recent model registry entries and evaluation details |

Example requests:

```powershell
Invoke-RestMethod "http://localhost:8000/health"
Invoke-RestMethod "http://localhost:8000/models"
Invoke-RestMethod "http://localhost:8000/predict?horizon=6"
```

The forecast endpoint returns the location, model version, issue timestamp, and forecast values. Future timestamps may have no corresponding actual value yet; actuals become available as time advances and new observations are ingested.

## Run locally

### Prerequisites

- Git
- Docker Desktop with Docker Compose
- Python 3.11+ (for local development and tests)

### 1. Clone the repository

```bash
git clone https://github.com/RiyaPandey06/cloud-native-data-pipeline-with-deep-learning.git
cd cloud-native-data-pipeline-with-deep-learning
```

### 2. Configure environment variables

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

Review `.env` and update values if needed. Do not commit your real `.env`, API credentials, webhook URLs, or secrets. The committed `.env.example` contains safe template values.

### 3. Start the services

```powershell
docker compose up --build
```

Once the containers are healthy:

- API: http://localhost:8000
- Interactive API docs: http://localhost:8000/docs
- Streamlit dashboard: http://localhost:8501

The scheduler performs the configured initial backfill and then continues with incremental hourly ingestion. The trainer waits for the minimum data requirement before scheduled retraining.

### 4. Run a manual pipeline window

```powershell
docker compose run --rm scheduler python scripts/run_pipeline.py --start 2026-08-01 --end 2026-10-29
```

The operation can be repeated safely because database writes use idempotent upserts.

### 5. Train models manually

```powershell
docker compose run --rm trainer python scripts/train_model.py --architecture all
```

You can also train one architecture:

```powershell
docker compose run --rm trainer python scripts/train_model.py --architecture lstm
docker compose run --rm trainer python scripts/train_model.py --architecture cnn
```

### Optional: Airflow

The project includes an optional Airflow DAG for local orchestration experiments:

```powershell
docker compose -f docker-compose.yml -f docker-compose.airflow.yml up --build db airflow
```

The Airflow path is optional and should not be interpreted as a verified production deployment.

## Testing and code quality

The project test suite and static checks were run during development:

- **37 pytest tests passed**
- Ruff lint checks passed
- Ruff format checks passed

To run the checks locally:

```powershell
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest -q
```

Additional evidence/report commands:

```powershell
python scripts/evidence.py idempotency
python scripts/evidence.py bad-batch
python scripts/evidence.py success-rate
python scripts/quality_report.py
python scripts/make_results.py
```

Install the pre-commit hooks:

```powershell
pre-commit install
```

The hooks include Ruff checks and a Gitleaks secret scan.

## Project structure

```text
.
├── .github/workflows/       # CI and optional deployment workflows
├── airflow/                 # Optional Airflow DAG
├── artifacts/               # Model artifacts are kept out of Git
├── deploy/gcp/               # Google Cloud deployment scripts
├── docs/
│   ├── architecture.png
│   ├── architecture.mmd
│   ├── RESULTS.md
│   ├── DATA_QUALITY_REPORT.md
│   ├── MODEL_CARD.md
│   ├── DEPLOYMENT.md
│   └── DESIGN_DECISIONS.md
├── scripts/                  # Pipeline, scheduler, training, and reports
├── src/air_quality_pipeline/ # Application and pipeline modules
├── tests/                    # Automated tests
├── docker-compose.yml
├── docker-compose.airflow.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Configuration

Common settings are provided through `.env.example`. They include:

| Setting | Purpose |
|---|---|
| Location name and coordinates | Select the forecast location |
| `BACKFILL_DAYS` | Initial historical backfill window (default 90 days) |
| `OVERLAP_HOURS` | Re-fetch overlap for late data/revisions (48 hours) |
| `MAX_NULL_SHARE` | Maximum tolerated key-null share (1%) |
| `RAW_RETENTION_DAYS` | Raw API payload retention (30 days) |
| `FAILURE_WEBHOOK_URL` | Optional quality/failure alert destination |

Check `.env.example` and the configuration module for the full list of supported variables and defaults.

## Limitations and next steps

- **Modelled target data:** CAMS ENSEMBLE output is not a ground-based pollution measurement.
- **Short operational history:** the current quality report contains one successful run only; collect several weeks of runs before making reliability claims.
- **Model performance:** Gradient Boosting and simple baselines outperform the neural models in the current hold-out evaluation. Improve feature engineering and gather more data before claiming a deep-learning performance advantage.
- **Deployment:** deployment scripts and an opt-in workflow are included, but this repository's current evidence does not establish that a live cloud deployment is running.
- **Airflow:** the optional DAG is included, but the design notes state that it has not been exercised against a live Airflow instance in this revision.
- **Screenshots:** only the architecture diagram is currently committed; add genuine runtime screenshots after launching the local stack.

## Documentation

- [Model results](docs/RESULTS.md)
- [Data quality report](docs/DATA_QUALITY_REPORT.md)
- [Model card and intended use](docs/MODEL_CARD.md)
- [Architecture and design decisions](docs/DESIGN_DECISIONS.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Data terms](docs/DATA_TERMS.md)
- [Portfolio evidence checklist](docs/DEMO_CHECKLIST.md)

## Data attribution

Air-quality data is obtained through the [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) and is based on the **Copernicus Atmosphere Monitoring Service (CAMS ENSEMBLE)**. Weather covariates are obtained from Open-Meteo. Review the provider's current terms and the project's [data terms notes](docs/DATA_TERMS.md). Data attribution is provided under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

---

<p align="center">
  Built as an end-to-end demonstration of data engineering, time-series forecasting, and ML system operations.
</p>
