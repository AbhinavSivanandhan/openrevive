#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env
tf_runtime init -input=false >/dev/null

CLUSTER_NAME="$(tf_runtime output -raw cluster_name 2>/dev/null || true)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name 2>/dev/null || true)"
PIPE_NAME="$(tf_runtime output -raw pipe_name 2>/dev/null || true)"

if [[ -z "$CLUSTER_NAME" ]]; then
  echo "No runtime Terraform outputs found; nothing to stop."
  exit 0
fi

if [[ -n "$PIPE_NAME" ]]; then
  echo "===== stop worker launch pipe ====="
  stop_pipe_if_present "$PIPE_NAME"
fi

if [[ -n "$API_SERVICE_NAME" ]]; then
  echo "===== scale API to zero ====="
  aws_cli ecs update-service \
    --cluster "$CLUSTER_NAME" \
    --service "$API_SERVICE_NAME" \
    --desired-count 0 \
    >/dev/null 2>&1 || true
fi

echo "===== stop all running demo tasks ====="
stop_all_ecs_tasks "$CLUSTER_NAME"
wait_for_ecs_tasks_to_stop "$CLUSTER_NAME"

echo "Cloud kill applied: pipe stopped, API scaled to zero, running tasks stopped."
