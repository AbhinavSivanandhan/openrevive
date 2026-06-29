#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

load_deploy_env

API_URL="$(tf_runtime output -raw api_url)"
AUTH=(-u "${BASIC_AUTH_USERNAME}:${BASIC_AUTH_PASSWORD}")

post_json() {
  local path="$1"
  local body="$2"

  curl \
    --fail-with-body \
    --silent \
    --show-error \
    "${AUTH[@]}" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: openrevive-demo-$(date +%s)-$RANDOM" \
    -d "$body" \
    "$API_URL$path"
}

json_field() {
  python3 -c \
    'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' \
    "$1"
}

WORKSPACE_JSON="$(
  post_json \
    "/v1/workspaces" \
    '{"name":"OpenRevive Public Demo"}'
)"

WORKSPACE_ID="$(printf '%s' "$WORKSPACE_JSON" | json_field id)"

COLLECTION_JSON="$(
  post_json \
    "/v1/workspaces/$WORKSPACE_ID/collections" \
    '{"name":"Python Documentation Research","description":"Public-source crawler demo."}'
)"

COLLECTION_ID="$(printf '%s' "$COLLECTION_JSON" | json_field id)"

CAMPAIGN_JSON="$(
  post_json \
    "/v1/collections/$COLLECTION_ID/crawl-runs" \
    '{
      "name":"Asyncio and exception hierarchy",
      "seed_urls":["https://docs.python.org/3/library/asyncio.html"],
      "allowed_domains":["docs.python.org"],
      "research_intent":"Understand asyncio task groups, cancellation, event loops, and exception hierarchy.",
      "max_pages":20,
      "max_depth":1,
      "request_timeout_seconds":20,
      "max_attempts":2
    }'
)"

CAMPAIGN_ID="$(printf '%s' "$CAMPAIGN_JSON" | json_field id)"

curl \
  --fail-with-body \
  --silent \
  --show-error \
  "${AUTH[@]}" \
  -X POST \
  "$API_URL/v1/collections/$COLLECTION_ID/crawl-runs/$CAMPAIGN_ID/start" \
  >/dev/null

echo "Public Python documentation campaign started."
echo
echo "After opening the deployed frontend, run this in browser DevTools once:"
printf "localStorage.setItem('openrevive.demo.collection-id', '%s')\n" \
  "$COLLECTION_ID"
