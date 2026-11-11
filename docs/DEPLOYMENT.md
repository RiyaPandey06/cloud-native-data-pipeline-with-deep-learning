# Deployment (GCP)

**Status: scripts written, not executed.** The provisioning script, workflow and schedules in this repo have not been run against a real GCP project. Expect to fix small issues on first run.

## Cost, honestly

- Cloud Run and Cloud Scheduler have free allowances; **Cloud SQL has none** (a `db-f1-micro` costs a few dollars a month). Set a budget alert first (`BILLING_ACCOUNT=... provision.sh` creates one).
- BigQuery's always-free allowance (1 TiB queries, 10 GiB storage per month) is confirmed on its pricing page and far above this project's volume, but the code targets PostgreSQL; using BigQuery would need a new repository layer.
- A free-tier `e2-micro` VM (US regions only; verify current terms at sign-up) has about 1 GB RAM: too small for Airflow, and tight for Postgres plus the API and dashboard. Run training elsewhere if you go that way.
- AWS: new accounts since 15 July 2025 get a Free plan (up to $200 credits) that ends after 6 months or when credits run out and then closes the account; a permanent URL needs the Paid plan. GCP is the default here for that reason.

## Steps

1. `PROJECT_ID=... GITHUB_REPO=owner/name BILLING_ACCOUNT=... ./deploy/gcp/provision.sh`
2. Copy the printed variables/secrets into GitHub (Settings → Secrets and variables → Actions).
3. Push to `main`. `deploy-gcp.yml` runs lint and tests, builds and pushes the image, deploys the API, the dashboard and two Cloud Run jobs, smoke-tests `/health`, and writes the push-to-live time to the job summary (target: under 10 minutes).
4. `PROJECT_ID=... ./deploy/gcp/schedule.sh` creates hourly (`:05`) and weekly (Sunday 03:00 UTC) Cloud Scheduler triggers.
5. Run `gcloud run jobs execute air-quality-hourly --wait` once to backfill, then let the schedule run. Optionally set `FAILURE_WEBHOOK_URL` on the jobs.
6. Start the monitoring clock: after 4 weeks, `python scripts/evidence.py success-rate`.

Deploy early: the 4-week run history is the proof, and nothing can shorten it.
