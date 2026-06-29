#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

load_deploy_env

ECS_CLUSTER_NAME="$(tf_runtime output -raw ecs_cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
WORKER_SERVICE_NAME="$(tf_runtime output -raw worker_service_name)"

aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER_NAME" \
  --service "$API_SERVICE_NAME" \
  --desired-count 0 \
  >/dev/null

aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER_NAME" \
  --service "$WORKER_SERVICE_NAME" \
  --desired-count 0 \
  >/dev/null

echo "API and worker are scaled to zero."
echo "Aurora, S3, ECR, Secrets Manager, ALB, and Vercel remain."
