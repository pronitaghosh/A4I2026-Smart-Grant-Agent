#!/usr/bin/env bash
# Deploy MCP Toolbox (BUILD_GUIDE Step 9) to Cloud Run. Run from the repo root in Cloud Shell:
#     bash agent/toolbox/deploy.sh
# Safe to re-run: existing service account, roles and secret are reused, and a changed
# tools.yaml is uploaded as a new secret version before redeploying.
#
# The project is never stored in the repo. It comes from, in order:
#   PROJECT_ID env var (set by the deploy pipeline)  ->  gcloud's current project.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: no project. Set PROJECT_ID, or run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi
# Region: REGION if set (pipeline), else the region the team's Cloud Run services already use,
# else us-west2 (the PRD's region).
REGION="${REGION:-$(gcloud run services list --project "${PROJECT_ID}" --format="value(region)" --limit=1 2>/dev/null || true)}"
REGION="${REGION:-us-west2}"
SA_NAME="toolbox-sa"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${IMAGE:-us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest}"
TOOLS_FILE="$(dirname "$0")/tools.yaml"

echo "== Project ${PROJECT_ID}, region ${REGION}"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "== Enabling APIs (Cloud Run, Secret Manager, BigQuery)"
gcloud services enable run.googleapis.com secretmanager.googleapis.com bigquery.googleapis.com

echo "== Service account ${SA}"
gcloud iam service-accounts describe "${SA}" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "${SA_NAME}" --display-name "MCP Toolbox"
for role in roles/bigquery.jobUser roles/bigquery.dataViewer roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:${SA}" --role "${role}" --condition=None --quiet >/dev/null
  echo "   granted ${role}"
done

echo "== Uploading tools.yaml as secret 'tools'"
if gcloud secrets describe tools >/dev/null 2>&1; then
  gcloud secrets versions add tools --data-file="${TOOLS_FILE}"
else
  gcloud secrets create tools --data-file="${TOOLS_FILE}"
fi

echo "== Deploying Cloud Run service 'toolbox' (min instances 1: no cold start on stage)"
gcloud run deploy toolbox \
  --image "${IMAGE}" \
  --service-account "${SA}" \
  --region "${REGION}" \
  --set-secrets "/app/tools.yaml=tools:latest" \
  --set-env-vars "PROJECT_ID=${PROJECT_ID}" \
  --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080" \
  --min-instances=1 \
  --no-allow-unauthenticated

URL="$(gcloud run services describe toolbox --region "${REGION}" --format 'value(status.url)')"

# Hackathon shortcut: the Toolbox only runs the read-only SQL in tools.yaml over this
# dataset, so we open it to unauthenticated calls. Lab organisations sometimes forbid
# public services by policy; if so, this step fails and the agent's service account
# needs the Cloud Run Invoker role on the service instead.
echo "== Allowing unauthenticated calls"
if gcloud run services add-iam-policy-binding toolbox --region "${REGION}" \
     --member=allUsers --role=roles/run.invoker --quiet >/dev/null 2>&1; then
  echo "   public: OK"
else
  echo "   BLOCKED by organisation policy. The service is private: grant roles/run.invoker"
  echo "   on 'toolbox' to the agent's service account, and send an ID token from the agent."
fi

echo
echo "Toolbox URL: ${URL}"
echo "Put this in agent/grant_agent/.env as TOOLBOX_URL=${URL}"
echo "Quick check: curl -s ${URL}/  (expect a short greeting, not an error page)"
