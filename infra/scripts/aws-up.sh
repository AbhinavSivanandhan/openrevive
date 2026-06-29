#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

for command in aws terraform docker git python3 curl; do
  require_command "$command"
done

load_deploy_env
require_clean_worktree
foundation_args

echo "===== applying durable foundation ====="
tf_foundation init
tf_foundation apply -auto-approve "${FOUNDATION_ARGS[@]}"

load_foundation_outputs

AUTH_SECRET_FILE="$(mktemp)"
trap 'rm -f "$AUTH_SECRET_FILE"' EXIT
chmod 600 "$AUTH_SECRET_FILE"

BASIC_AUTH_USERNAME="$BASIC_AUTH_USERNAME" \
BASIC_AUTH_PASSWORD="$BASIC_AUTH_PASSWORD" \
BASIC_AUTH_USERNAME_2="$BASIC_AUTH_USERNAME_2" \
BASIC_AUTH_PASSWORD_2="$BASIC_AUTH_PASSWORD_2" \
python3 - "$AUTH_SECRET_FILE" <<'PY'
import json
import os
import sys

path = sys.argv[1]

payload = {
    "username": os.environ["BASIC_AUTH_USERNAME"],
    "password": os.environ["BASIC_AUTH_PASSWORD"],
    "username_2": os.environ["BASIC_AUTH_USERNAME_2"],
    "password_2": os.environ["BASIC_AUTH_PASSWORD_2"],
}

with open(path, "w", encoding="utf-8") as file:
    json.dump(payload, file)
PY

echo "===== storing Basic Auth values in Secrets Manager ====="
aws secretsmanager put-secret-value \
  --region "$AWS_REGION" \
  --secret-id "$BASIC_AUTH_SECRET_ARN" \
  --secret-string "file://$AUTH_SECRET_FILE" \
  >/dev/null

IMAGE_TAG="$(image_tag)"
ECR_REGISTRY="${ECR_REPOSITORY_URL%%/*}"

echo "===== building and publishing one API/worker image ====="
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login \
      --username AWS \
      --password-stdin "$ECR_REGISTRY"

docker build \
  -f "$ROOT/services/api/Dockerfile" \
  -t "openrevive-api:$IMAGE_TAG" \
  "$ROOT"

docker tag \
  "openrevive-api:$IMAGE_TAG" \
  "$ECR_REPOSITORY_URL:$IMAGE_TAG"

docker push "$ECR_REPOSITORY_URL:$IMAGE_TAG"

runtime_args 0 0 "$IMAGE_TAG"

echo "===== applying runtime with services stopped ====="
tf_runtime init
tf_runtime apply -auto-approve "${RUNTIME_ARGS[@]}"

ECS_CLUSTER_NAME="$(tf_runtime output -raw ecs_cluster_name)"
MIGRATION_TASK_DEFINITION_ARN="$(
  tf_runtime output -raw migration_task_definition_arn
)"

SUBNET_LIST="$(
  python3 - "$PUBLIC_SUBNET_IDS_JSON" <<'PY'
import json
import sys

print(",".join(json.loads(sys.argv[1])))
PY
)"

echo "===== running Alembic migration as a one-off ECS task ====="
TASK_ARN="$(
  aws ecs run-task \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER_NAME" \
    --launch-type FARGATE \
    --task-definition "$MIGRATION_TASK_DEFINITION_ARN" \
    --network-configuration \
      "awsvpcConfiguration={subnets=[$SUBNET_LIST],securityGroups=[$WORKER_TASK_SECURITY_GROUP_ID],assignPublicIp=ENABLED}" \
    --query "tasks[0].taskArn" \
    --output text
)"

if [[ -z "$TASK_ARN" || "$TASK_ARN" == "None" ]]; then
  echo "Migration task did not start." >&2
  exit 1
fi

aws ecs wait tasks-stopped \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER_NAME" \
  --tasks "$TASK_ARN"

MIGRATION_EXIT_CODE="$(
  aws ecs describe-tasks \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER_NAME" \
    --tasks "$TASK_ARN" \
    --query "tasks[0].containers[0].exitCode" \
    --output text
)"

if [[ "$MIGRATION_EXIT_CODE" != "0" ]]; then
  echo "Migration task failed." >&2
  echo "Inspect /openrevive/demo/migration in CloudWatch Logs." >&2
  exit 1
fi

runtime_args 1 1 "$IMAGE_TAG"

echo "===== starting API and worker ====="
tf_runtime apply -auto-approve "${RUNTIME_ARGS[@]}"

ECS_CLUSTER_NAME="$(tf_runtime output -raw ecs_cluster_name)"
API_SERVICE_NAME="$(tf_runtime output -raw api_service_name)"
WORKER_SERVICE_NAME="$(tf_runtime output -raw worker_service_name)"
API_URL="$(tf_runtime output -raw api_url)"

aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER_NAME" \
  --services "$API_SERVICE_NAME" "$WORKER_SERVICE_NAME"

cat > "$RUNTIME_ENV" <<CONFIG
DEPLOYED_IMAGE_TAG=$(printf '%q' "$IMAGE_TAG")
CONFIG
chmod 600 "$RUNTIME_ENV"

echo
echo "Backend deployment started."
echo "API origin: $API_URL"
echo "Public health route: $API_URL/health"
echo
echo "The custom DNS record may need a short propagation period."
