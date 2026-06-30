#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_command aws
require_command curl
require_command terraform
require_command python3

export AWS_PAGER=""

"$SCRIPT_DIR/cloud-check.sh"

load_cloud_env
load_foundation_outputs

API_BASE_URL="$(tf_runtime output -raw api_base_url)"
WORKER_LOG_GROUP="$(tf_runtime output -raw worker_log_group_name)"

SMOKE_SEED_URL="${SMOKE_SEED_URL:-https://docs.python.org/3/library/asyncio.html}"
MAX_POLLS="${MAX_POLLS:-24}"
POLL_SECONDS="${POLL_SECONDS:-5}"

json_field() {
  local field="$1"

  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
print(payload[sys.argv[1]])
' "$field"
}

SEED_DOMAIN="$(
  python3 -c '
from urllib.parse import urlsplit
import sys

domain = urlsplit(sys.argv[1]).hostname
if not domain:
    raise SystemExit("SMOKE_SEED_URL must be an absolute URL.")

print(domain)
' "$SMOKE_SEED_URL"
)"

RUN_SUFFIX="$(date -u +%Y%m%dT%H%M%SZ)"
START_TIME_MS="$(( $(date +%s) * 1000 ))"
IDEMPOTENCY_KEY="cloud-smoke-${RUN_SUFFIX}"

echo
echo "===== create bounded smoke campaign ====="

WORKSPACE_PAYLOAD="$(
  RUN_SUFFIX="$RUN_SUFFIX" python3 -c '
import json
import os

print(json.dumps({
    "name": f"Cloud Smoke {os.environ["RUN_SUFFIX"]}"
}))
'
)"

WORKSPACE_JSON="$(
  curl -fsS \
    -X POST "$API_BASE_URL/v1/workspaces" \
    -H 'Content-Type: application/json' \
    -d "$WORKSPACE_PAYLOAD"
)"

WORKSPACE_ID="$(printf '%s' "$WORKSPACE_JSON" | json_field id)"

COLLECTION_JSON="$(
  curl -fsS \
    -X POST "$API_BASE_URL/v1/workspaces/$WORKSPACE_ID/collections" \
    -H 'Content-Type: application/json' \
    -d '{
      "name":"One-page event-driven smoke",
      "description":"Automated AWS infrastructure verification campaign."
    }'
)"

COLLECTION_ID="$(printf '%s' "$COLLECTION_JSON" | json_field id)"

RUN_PAYLOAD="$(
  RUN_SUFFIX="$RUN_SUFFIX" \
  SMOKE_SEED_URL="$SMOKE_SEED_URL" \
  SEED_DOMAIN="$SEED_DOMAIN" \
  python3 -c '
import json
import os

print(json.dumps({
    "name": f"Cloud smoke {os.environ["RUN_SUFFIX"]}",
    "seed_urls": [os.environ["SMOKE_SEED_URL"]],
    "allowed_domains": [os.environ["SEED_DOMAIN"]],
    "research_intent": "Verify the AWS event-driven crawler path.",
    "max_pages": 1,
    "max_depth": 0,
    "request_timeout_seconds": 20,
    "max_attempts": 2,
}))
'
)"

RUN_JSON="$(
  curl -fsS \
    -X POST "$API_BASE_URL/v1/collections/$COLLECTION_ID/crawl-runs" \
    -H 'Content-Type: application/json' \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
    -d "$RUN_PAYLOAD"
)"

RUN_ID="$(printf '%s' "$RUN_JSON" | json_field id)"

echo "Created pending campaign: $RUN_ID"

START_JSON="$(
  curl -fsS \
    -X POST "$API_BASE_URL/v1/collections/$COLLECTION_ID/crawl-runs/$RUN_ID/start"
)"

START_STATUS="$(printf '%s' "$START_JSON" | json_field status)"

if [[ "$START_STATUS" != "RUNNING" ]]; then
  echo "FAIL: campaign did not enter RUNNING state." >&2
  exit 1
fi

echo "PASS: API committed RUNNING campaign and published a wake-up event."
echo
echo "===== poll crawl completion ====="

DETAIL_JSON=""
TERMINAL_STATUS=""

for attempt in $(seq 1 "$MAX_POLLS"); do
  DETAIL_JSON="$(
    curl -fsS \
      "$API_BASE_URL/v1/collections/$COLLECTION_ID/crawl-runs/$RUN_ID"
  )"

  CURRENT_STATUS="$(printf '%s' "$DETAIL_JSON" | json_field status)"
  echo "Poll $attempt/$MAX_POLLS: $CURRENT_STATUS"

  case "$CURRENT_STATUS" in
    SUCCEEDED|PARTIALLY_SUCCEEDED|FAILED|CANCELLED)
      TERMINAL_STATUS="$CURRENT_STATUS"
      break
      ;;
  esac

  sleep "$POLL_SECONDS"
done

if [[ "$TERMINAL_STATUS" != "SUCCEEDED" ]]; then
  echo "FAIL: expected SUCCEEDED, got ${TERMINAL_STATUS:-timeout}." >&2
  printf '%s\n' "$DETAIL_JSON" | python3 -m json.tool >&2
  exit 1
fi

echo "PASS: crawl reached SUCCEEDED."

DOCUMENTS_JSON="$(
  curl -fsS \
    "$API_BASE_URL/v1/collections/$COLLECTION_ID/crawl-runs/$RUN_ID/documents"
)"

DOCUMENT_TOTAL="$(
  printf '%s' "$DOCUMENTS_JSON" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["total"])'
)"

if [[ "$DOCUMENT_TOTAL" -lt 1 ]]; then
  echo "FAIL: API returned no persisted documents." >&2
  exit 1
fi

RAW_OBJECT_KEY="$(
  printf '%s' "$DOCUMENTS_JSON" |
    python3 -c '
import json
import sys

print(json.load(sys.stdin)["items"][0]["raw_object_key"])
'
)"

echo "PASS: API returned $DOCUMENT_TOTAL persisted document(s)."

CONTENT_LENGTH="$(
  aws s3api head-object \
    --bucket "$ARTIFACTS_BUCKET_NAME" \
    --key "$RAW_OBJECT_KEY" \
    --query 'ContentLength' \
    --output text
)"

if [[ ! "$CONTENT_LENGTH" =~ ^[0-9]+$ || "$CONTENT_LENGTH" -le 0 ]]; then
  echo "FAIL: persisted S3 artifact is empty or missing." >&2
  exit 1
fi

echo "PASS: S3 artifact exists ($CONTENT_LENGTH bytes)."
echo
echo "===== verify worker drain ====="

DRAIN_LOGS=""

for attempt in $(seq 1 12); do
  DRAIN_LOGS="$(
    aws logs filter-log-events \
      --log-group-name "$WORKER_LOG_GROUP" \
      --start-time "$START_TIME_MS" \
      --filter-pattern '"drained after"' \
      --query 'events[].message' \
      --output text \
      || true
  )"

  if [[ -n "$DRAIN_LOGS" && "$DRAIN_LOGS" != "None" ]]; then
    break
  fi

  sleep 5
done

if [[ -z "$DRAIN_LOGS" || "$DRAIN_LOGS" == "None" ]]; then
  echo "FAIL: no worker drain log appeared." >&2
  exit 1
fi

echo "PASS: worker drained and exited."
printf '%s\n' "$DRAIN_LOGS"

echo
echo "===== cloud-smoke completed ====="
echo "workspace=$WORKSPACE_ID"
echo "collection=$COLLECTION_ID"
echo "crawl_run=$RUN_ID"
echo "raw_object_key=$RAW_OBJECT_KEY"
