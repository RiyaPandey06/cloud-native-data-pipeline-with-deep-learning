# Design Decisions

**Airflow vs cron.** The live deployment uses a cron-style trigger (Cloud Scheduler → Cloud Run job, or `scripts/scheduler.py` in Docker). Airflow's Docker Compose guide asks for at least 4 GB of memory (ideally 8 GB), so do not assume it fits a small free VM. The pipeline is written as separate stage methods (`start_run → ingest → validate → load → score`) that read each other's output from the database, so the identical code also runs as the Airflow DAG in `airflow/dags/` (local, for screenshots). Each DAG task calls `scripts/run_stage.py` in a separate virtualenv so the project's SQLAlchemy 2 pins don't clash with Airflow's. The DAG has not been exercised against a live Airflow in this repo's last revision; run it locally before screenshotting.

**Why upserts.** Retries, overlapping incremental windows and manual backfills all re-deliver rows. A unique key on `(location_id, ts_utc, source)` with `ON CONFLICT DO UPDATE` makes every load idempotent and lets revised source values overwrite old ones. `scripts/evidence.py idempotency` demonstrates it.

**Why pandas, not Spark.** About 8,760 hourly rows per location per year fit in memory many times over. Spark would add cost and moving parts with no benefit; it would make sense for many locations, minutes-level data or image data.

**Late and missing data.** Open-Meteo returns the whole requested day including future model forecasts, and the newest hours can lag. Future hours and trailing unpublished hours are trimmed (counted in `pipeline_runs.details`), not treated as errors. Interior nulls are judged by the gate (tolerated up to `MAX_NULL_SHARE`, then dropped; more fails). Each run re-fetches `OVERLAP_HOURS` (48) so late arrivals and source revisions are picked up, and a missed slot is covered by the next run.

**Source schema changes.** The schema check fails the run if required columns are missing or non-numeric; raw payloads are kept, so after fixing the mapping the window can be re-run without refetching assumptions. The failure alerts via webhook.

**Quarantine vs fail.** Implausible values (`range`) quarantine the batch: stored raw, never loaded, run marked `quarantined`. Structural problems (schema, duplicates, large gaps, too many nulls) fail it. Warnings load the batch but alert.

**Alerts live in the pipeline,** not in the scheduler loop, so the Cloud Run job, the cron worker and Airflow all alert the same way.

**Model selection.** Both networks are trained weekly; the one with lower *validation* MAE is active (never chosen on test). Baselines are reported next to it, and a baseline winning is a valid result.

**Raw retention.** Raw payloads are purged after `RAW_RETENTION_DAYS` (30) following a clean run to keep storage small.
