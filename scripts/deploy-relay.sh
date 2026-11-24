#!/usr/bin/env bash
# Deploys the MCP relay (services/relay) to Cloud Run from this Mac with Cloud Build (no GitHub Actions).
# Nothing runs without --yes: deploying publishes a public endpoint (owner's go required, plan T14/L7-08).
#   FIRRA_GCP_PROJECT=<project> FIRRA_GCP_REGION=europe-west1 scripts/deploy-relay.sh --yes
set -euo pipefail
cd "$(dirname "$0")/.."

[[ "${1:-}" == "--yes" ]] || { echo "Dry run. Re-run with --yes after the owner's go."; exit 2; }
: "${FIRRA_GCP_PROJECT:?set FIRRA_GCP_PROJECT}"
region="${FIRRA_GCP_REGION:-europe-west1}"
service="firra-relay"
image="$region-docker.pkg.dev/$FIRRA_GCP_PROJECT/firra/$service:$(git rev-parse --short HEAD)"

scripts/check.sh worker
cfg="$(mktemp)"
trap 'rm -f "$cfg"' EXIT
cat >"$cfg" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, services/relay/Dockerfile, -t, $image, .]
images: [$image]
YAML
gcloud builds submit --project "$FIRRA_GCP_PROJECT" --config "$cfg" .
# One instance: grants live in memory (docs/plans/local/relay.md section 8); WebSockets live up to the request timeout.
gcloud run deploy "$service" --project "$FIRRA_GCP_PROJECT" --region "$region" --image "$image" \
  --allow-unauthenticated --min-instances 1 --max-instances 1 --concurrency 200 --timeout 3600 \
  --cpu 1 --memory 256Mi --port 8080
# Cloud Run's own request log records the URL path, and the path holds the link secret: keep those entries out of Cloud Logging.
if ! gcloud logging sinks describe _Default --project "$FIRRA_GCP_PROJECT" --format='value(exclusions[].name)' | grep -q firra-relay-requests; then
  gcloud logging sinks update _Default --project "$FIRRA_GCP_PROJECT" \
    --add-exclusion="name=firra-relay-requests,filter=resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$service\" AND logName:\"run.googleapis.com%2Frequests\""
fi
url="$(gcloud run services describe "$service" --project "$FIRRA_GCP_PROJECT" --region "$region" --format 'value(status.url)')"
curl -fsS "$url/health" && echo " <- $url"
