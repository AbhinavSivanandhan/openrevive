#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env
load_foundation_outputs

echo "===== foundation ====="
echo "Aurora: $(tf_foundation output -raw aurora_cluster_identifier)"
echo "Artifacts: $ARTIFACTS_BUCKET_NAME"
echo "Queue: $CRAWL_EVENT_QUEUE_URL"

echo
echo "===== runtime ====="
CLUSTER_NAME="$(tf_runtime output -raw cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
PIPE_NAME="$(tf_runtime output -raw pipe_name)"

aws ecs describe-services \
  --cluster "$CLUSTER_NAME" \
  --services "$API_SERVICE_NAME" \
  --query 'services[].{service:serviceName,desired:desiredCount,running:runningCount,pending:pendingCount}' \
  --output table

aws pipes describe-pipe \
  --name "$PIPE_NAME" \
  --query '{name:Name,current:CurrentState,desired:DesiredState}' \
  --output table

aws sqs get-queue-attributes \
  --queue-url "$CRAWL_EVENT_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --output table

echo
echo "API: $(tf_runtime output -raw api_base_url)"
