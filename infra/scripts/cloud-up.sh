#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command docker
require_command terraform
require_command python3

load_cloud_env

echo "===== initialize and apply foundation ====="
tf_foundation init -upgrade
foundation_apply
load_foundation_outputs

echo "===== build and push ARM64 API/worker image ====="
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login \
      --username AWS \
      --password-stdin \
      "$(cut -d/ -f1 <<<"$ECR_REPOSITORY_URL")"

IMAGE_TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
IMAGE_URI="${ECR_REPOSITORY_URL}:${IMAGE_TAG}"

docker buildx build \
  --platform linux/arm64 \
  --push \
  --tag "$IMAGE_URI" \
  --file "$ROOT/services/api/Dockerfile" \
  "$ROOT"

echo "===== create runtime with API stopped ====="
write_runtime_vars "$IMAGE_URI" 0
tf_runtime init -upgrade
tf_runtime apply -auto-approve

CLUSTER_NAME="$(tf_runtime output -raw cluster_name)"
MIGRATION_TASK_DEFINITION_ARN="$(
  tf_runtime output -raw migration_task_definition_arn
)"

echo "===== run Alembic migration task ====="
TASK_ARN="$(
  aws ecs run-task \
    --cluster "$CLUSTER_NAME" \
    --launch-type FARGATE \
    --task-definition "$MIGRATION_TASK_DEFINITION_ARN" \
    --network-configuration "$(task_network_configuration)" \
    --query 'tasks[0].taskArn' \
    --output text
)"

[[ "$TASK_ARN" != "None" && -n "$TASK_ARN" ]] \
  || fail "Migration task was not started."

aws ecs wait tasks-stopped \
  --cluster "$CLUSTER_NAME" \
  --tasks "$TASK_ARN"

EXIT_CODE="$(
  aws ecs describe-tasks \
    --cluster "$CLUSTER_NAME" \
    --tasks "$TASK_ARN" \
    --query 'tasks[0].containers[0].exitCode' \
    --output text
)"

[[ "$EXIT_CODE" == "0" ]] \
  || fail "Migration task failed with exit code: $EXIT_CODE"

echo "===== start API service ====="
write_runtime_vars "$IMAGE_URI" 1
tf_runtime apply -auto-approve

API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
aws ecs wait services-stable \
  --cluster "$CLUSTER_NAME" \
  --services "$API_SERVICE_NAME"

API_BASE_URL="$(tf_runtime output -raw api_base_url)"

echo "===== health check ====="
curl --fail --silent --show-error \
  --max-time 20 \
  "$API_BASE_URL/health"

echo
echo "Deployment complete."
echo "API: $API_BASE_URL"
echo "Queue: $CRAWL_EVENT_QUEUE_URL"
