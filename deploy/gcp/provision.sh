#!/usr/bin/env bash
# One-time GCP setup for the pipeline: APIs, Artifact Registry, Cloud SQL (PostgreSQL),
# the DATABASE_URL secret, service accounts and GitHub OIDC (Workload Identity Federation).
#
#   PROJECT_ID=my-project GITHUB_REPO=my-user/my-repo ./deploy/gcp/provision.sh
#
# Prints the GitHub repository variables and secrets to set at the end. Read docs/DEPLOYMENT.md
# first: Cloud SQL is NOT free. Set a budget alert (BILLING_ACCOUNT=... enables one below).
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${GITHUB_REPO:?set GITHUB_REPO as owner/name}"
REGION="${REGION:-us-central1}"
AR_REPOSITORY="${AR_REPOSITORY:-air-quality}"
SQL_INSTANCE="${SQL_INSTANCE:-air-quality-db}"
SQL_TIER="${SQL_TIER:-db-f1-micro}"
DB_NAME="${DB_NAME:-air_quality}"
DB_USER="${DB_USER:-pipeline}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-15USD}"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com iam.googleapis.com

echo "==> Artifact Registry"
gcloud artifacts repositories describe "$AR_REPOSITORY" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$AR_REPOSITORY" \
    --repository-format docker --location "$REGION"

echo "==> Cloud SQL (PostgreSQL 16, $SQL_TIER) - this takes several minutes"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version POSTGRES_16 --tier "$SQL_TIER" --region "$REGION" \
    --storage-type HDD --storage-size 10GB --availability-type zonal \
    --no-storage-auto-increase
fi
gcloud sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null 2>&1 ||
  gcloud sql databases create "$DB_NAME" --instance "$SQL_INSTANCE"
DB_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
if gcloud sql users list --instance "$SQL_INSTANCE" --format='value(name)' | grep -qx "$DB_USER"; then
  gcloud sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"
else
  gcloud sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"
fi
CONNECTION_NAME="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"

echo "==> Secret Manager: DATABASE_URL"
DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"
if gcloud secrets describe DATABASE_URL >/dev/null 2>&1; then
  printf '%s' "$DATABASE_URL" | gcloud secrets versions add DATABASE_URL --data-file=-
else
  printf '%s' "$DATABASE_URL" | gcloud secrets create DATABASE_URL --data-file=- \
    --replication-policy automatic
fi

echo "==> Service accounts"
create_sa() { gcloud iam service-accounts describe "$1@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$1" --display-name "$2"; }
create_sa air-quality-runtime "Air quality Cloud Run runtime"
create_sa air-quality-deployer "Air quality GitHub deployer"
create_sa air-quality-scheduler "Air quality Cloud Scheduler"
RUNTIME_SA="air-quality-runtime@$PROJECT_ID.iam.gserviceaccount.com"
DEPLOYER_SA="air-quality-deployer@$PROJECT_ID.iam.gserviceaccount.com"
SCHEDULER_SA="air-quality-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
grant() { gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$1" \
  --role "$2" --condition=None >/dev/null; }
grant "$RUNTIME_SA" roles/cloudsql.client
grant "$DEPLOYER_SA" roles/run.admin
grant "$DEPLOYER_SA" roles/artifactregistry.writer
grant "$SCHEDULER_SA" roles/run.invoker
gcloud secrets add-iam-policy-binding DATABASE_URL \
  --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:$DEPLOYER_SA" --role roles/iam.serviceAccountUser >/dev/null

echo "==> GitHub OIDC (Workload Identity Federation)"
gcloud iam workload-identity-pools describe github --location global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create github --location global --display-name "GitHub"
gcloud iam workload-identity-pools providers describe github-oidc \
  --workload-identity-pool github --location global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github-oidc \
    --workload-identity-pool github --location global \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='${GITHUB_REPO}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}" >/dev/null
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-oidc"

if [[ -n "${BILLING_ACCOUNT:-}" ]]; then
  echo "==> Budget alert ($BUDGET_AMOUNT per month)"
  gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" \
    --display-name "air-quality" --budget-amount "$BUDGET_AMOUNT" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0 ||
    echo "Budget creation failed; create one in the console before leaving this running."
else
  echo "!! BILLING_ACCOUNT not set: no budget alert created. Create one before deploying."
fi

cat <<OUT

Done. In GitHub: Settings > Secrets and variables > Actions

Repository variables
  ENABLE_GCP_DEPLOY   = true
  GCP_PROJECT_ID      = ${PROJECT_ID}
  GCP_REGION          = ${REGION}
  AR_REPOSITORY       = ${AR_REPOSITORY}
  CLOUDSQL_INSTANCE   = ${CONNECTION_NAME}
  RUNTIME_SERVICE_ACCOUNT = ${RUNTIME_SA}

Repository secrets
  GCP_WIF_PROVIDER    = ${WIF_PROVIDER}
  GCP_SERVICE_ACCOUNT = ${DEPLOYER_SA}

After the first successful deploy, create the schedules:
  PROJECT_ID=${PROJECT_ID} REGION=${REGION} ./deploy/gcp/schedule.sh
OUT
