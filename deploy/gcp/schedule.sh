#!/usr/bin/env bash
# Create (or update) the Cloud Scheduler triggers for the two Cloud Run jobs.
# Run once after the first deploy has created the jobs.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SCHEDULER_SA="air-quality-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
HOURLY_CRON="${HOURLY_CRON:-5 * * * *}"   # five past every hour
WEEKLY_CRON="${WEEKLY_CRON:-0 3 * * 0}"   # Sundays 03:00 UTC

upsert() {
  local name="$1" cron="$2" job="$3"
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job}:run"
  if gcloud scheduler jobs describe "$name" --location "$REGION" >/dev/null 2>&1; then
    verb=update
  else
    verb=create
  fi
  gcloud scheduler jobs "$verb" http "$name" --location "$REGION" \
    --schedule "$cron" --time-zone "UTC" --uri "$uri" --http-method POST \
    --oauth-service-account-email "$SCHEDULER_SA" \
    --attempt-deadline 180s --max-retry-attempts 2
}

upsert air-quality-hourly-trigger "$HOURLY_CRON" air-quality-hourly
upsert air-quality-weekly-train-trigger "$WEEKLY_CRON" air-quality-weekly-train
echo "Schedules created. Trigger one now with:"
echo "  gcloud run jobs execute air-quality-hourly --region ${REGION} --wait"
