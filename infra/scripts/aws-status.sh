#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

load_deploy_env

echo "===== foundation outputs ====="
tf_foundation output

echo
echo "===== runtime outputs ====="
tf_runtime output

echo
echo "===== ECS services ====="
ECS_CLUSTER_NAME="$(tf_runtime output -raw ecs_cluster_name)"

aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER_NAME" \
  --services \
    "$(tf_runtime output -raw api_service_name)" \
    "$(tf_runtime output -raw worker_service_name)" \
  --query 'services[].{service:serviceName,status:status,desired:desiredCount,running:runningCount,pending:pendingCount}' \
  --output table
