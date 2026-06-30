#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env

CLUSTER_NAME="$(tf_runtime output -raw cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
PIPE_NAME="$(tf_runtime output -raw pipe_name)"

echo "===== stop worker launch pipe ====="
aws pipes stop-pipe --name "$PIPE_NAME" >/dev/null 2>&1 || true

echo "===== scale API to zero ====="
aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$API_SERVICE_NAME" \
  --desired-count 0 \
  >/dev/null

echo "===== stop all running demo tasks ====="
TASKS="$(
  aws ecs list-tasks \
    --cluster "$CLUSTER_NAME" \
    --desired-status RUNNING \
    --query 'taskArns[]' \
    --output text
)"

for task in $TASKS; do
  aws ecs stop-task \
    --cluster "$CLUSTER_NAME" \
    --task "$task" \
    --reason "OpenRevive cloud-kill" \
    >/dev/null
done

echo "Cloud kill applied: pipe stopped, API scaled to zero, running tasks stopped."
