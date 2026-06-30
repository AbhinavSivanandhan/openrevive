#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env

CLUSTER_NAME="$(tf_runtime output -raw cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
PIPE_NAME="$(tf_runtime output -raw pipe_name)"

aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$API_SERVICE_NAME" \
  --desired-count 1 \
  >/dev/null

aws pipes start-pipe --name "$PIPE_NAME" >/dev/null 2>&1 || true

echo "API scaling resumed and worker launch pipe started."
