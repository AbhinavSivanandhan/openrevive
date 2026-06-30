#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_command aws
require_command curl
require_command terraform
require_command python3

export AWS_PAGER=""

assert_equal() {
  local actual="$1"
  local expected="$2"
  local label="$3"

  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $label — expected '$expected', got '$actual'." >&2
    exit 1
  fi

  echo "PASS: $label ($actual)"
}

load_private_access_credentials() {
  local credentials_file="$INFRA/.local/basic-auth.json"

  [[ -f "$credentials_file" ]] || \
    fail "Missing private-access credential file: $credentials_file"

  BASIC_AUTH_CREDENTIALS="$(
    python3 - "$credentials_file" <<'PY2'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

username = payload.get("username")
password = payload.get("password")

if not isinstance(username, str) or not username.strip():
    raise SystemExit("Credential file has no valid username.")

if not isinstance(password, str) or len(password) < 24:
    raise SystemExit("Credential file has no valid password.")

print(f"{username}:{password}")
PY2
  )"

  [[ -n "$BASIC_AUTH_CREDENTIALS" ]] || \
    fail "Could not load private-access credentials."
}


load_cloud_env
load_foundation_outputs

API_BASE_URL="$(tf_runtime output -raw api_base_url)"
CLUSTER_NAME="$(tf_runtime output -raw cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
PIPE_NAME="$(tf_runtime output -raw pipe_name)"
AURORA_CLUSTER_ID="$(tf_foundation output -raw aurora_cluster_identifier)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "===== OpenRevive cloud-check ====="
echo "Account: $ACCOUNT_ID"
echo "Region:  $AWS_REGION"
echo

echo "----- API health -----"
HEALTH_JSON="$(curl -fsS --max-time 20 "$API_BASE_URL/health")"
HEALTH_STATUS="$(
  printf '%s' "$HEALTH_JSON" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])'
)"
assert_equal "$HEALTH_STATUS" "ok" "ALB → ECS API health"
echo

echo "----- ECS API service -----"
read -r API_DESIRED API_RUNNING < <(
  aws ecs describe-services \
    --cluster "$CLUSTER_NAME" \
    --services "$API_SERVICE_NAME" \
    --query 'services[0].[desiredCount,runningCount]' \
    --output text
)

assert_equal "$API_DESIRED" "1" "API desired count"
assert_equal "$API_RUNNING" "1" "API running count"
echo

echo "----- Aurora -----"
AURORA_STATUS="$(
  aws rds describe-db-clusters \
    --db-cluster-identifier "$AURORA_CLUSTER_ID" \
    --query 'DBClusters[0].Status' \
    --output text
)"
assert_equal "$AURORA_STATUS" "available" "Aurora PostgreSQL"
echo

echo "----- EventBridge Pipe -----"
PIPE_STATE="$(
  aws pipes describe-pipe \
    --name "$PIPE_NAME" \
    --query 'CurrentState' \
    --output text
)"
assert_equal "$PIPE_STATE" "RUNNING" "SQS → EventBridge Pipe"
echo

echo "----- SQS queue -----"
aws sqs get-queue-attributes \
  --queue-url "$CRAWL_EVENT_QUEUE_URL" \
  --attribute-names \
    ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible \
  --query 'Attributes' \
  --output table
echo

echo "----- S3 lifecycle -----"
aws s3api get-bucket-lifecycle-configuration \
  --bucket "$ARTIFACTS_BUCKET_NAME" \
  --query 'Rules[].{rule:ID,status:Status,expiryDays:Expiration.Days}' \
  --output table
echo

echo "----- ECR image -----"
IMAGE_COUNT="$(
  aws ecr describe-images \
    --repository-name "${PROJECT_NAME}-${ENVIRONMENT}-api" \
    --query 'length(imageDetails)' \
    --output text
)"

if [[ "$IMAGE_COUNT" == "0" || "$IMAGE_COUNT" == "None" ]]; then
  echo "FAIL: ECR has no deployable image." >&2
  exit 1
fi

echo "PASS: ECR has $IMAGE_COUNT image(s)."
echo

echo "----- AWS Budget -----"
BUDGET_NAME="${PROJECT_NAME}-${ENVIRONMENT}-monthly"

aws budgets describe-budget \
  --account-id "$ACCOUNT_ID" \
  --budget-name "$BUDGET_NAME" \
  --query 'Budget.{name:BudgetName,limit:BudgetLimit.Amount,unit:BudgetLimit.Unit}' \
  --output table

echo "PASS: AWS Budget resource exists."
echo "NOTE: verify the budget confirmation email separately."
echo

if [[ -n "${FRONTEND_URL:-}" ]]; then
  [[ "$FRONTEND_URL" == https://* ]] ||     fail "FRONTEND_URL must use HTTPS for private-access verification."

  load_private_access_credentials

  echo "----- Vercel proxy -----"

  FRONTEND_HEALTH="$(
    curl -fsS --max-time 20       --user "$BASIC_AUTH_CREDENTIALS"       "${FRONTEND_URL%/}/api/health"
  )"

  FRONTEND_STATUS="$(
    printf '%s' "$FRONTEND_HEALTH" |
      python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])'
  )"

  assert_equal "$FRONTEND_STATUS" "ok" "Vercel → AWS API proxy"
  echo
fi

echo "PASS: cloud-check completed."
echo "API: $API_BASE_URL"
