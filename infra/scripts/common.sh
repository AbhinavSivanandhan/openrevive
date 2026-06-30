#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA="$ROOT/infra"
FOUNDATION="$INFRA/foundation"
RUNTIME="$INFRA/runtime"
CLOUD_ENV="$INFRA/.local/cloud.env"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

load_cloud_env() {
  mkdir -p "$INFRA/.local"

  if [[ ! -f "$CLOUD_ENV" ]]; then
    cat > "$CLOUD_ENV" <<'EOF'
AWS_PROFILE=openrevive
AWS_REGION=ap-south-1
PROJECT_NAME=openrevive
ENVIRONMENT=demo
BUDGET_LIMIT_USD=10
BUDGET_EMAIL=
EOF
    echo "Created $CLOUD_ENV."
  fi

  set -a
  # shellcheck disable=SC1090
  source "$CLOUD_ENV"
  set +a

  : "${AWS_PROFILE:=openrevive}"
  : "${AWS_REGION:=ap-south-1}"
  : "${PROJECT_NAME:=openrevive}"
  : "${ENVIRONMENT:=demo}"
  : "${BUDGET_LIMIT_USD:=10}"

  if [[ -z "${BUDGET_EMAIL:-}" ]]; then
    read -r -p "Budget alert email: " BUDGET_EMAIL
    [[ -n "$BUDGET_EMAIL" ]] || fail "Budget alert email is required."
    printf '\nBUDGET_EMAIL=%q\n' "$BUDGET_EMAIL" >> "$CLOUD_ENV"
  fi

  export AWS_PROFILE AWS_REGION PROJECT_NAME ENVIRONMENT
  export BUDGET_LIMIT_USD BUDGET_EMAIL
}

tf_foundation() {
  terraform -chdir="$FOUNDATION" "$@"
}

tf_runtime() {
  terraform -chdir="$RUNTIME" "$@"
}

foundation_apply() {
  tf_foundation apply -auto-approve \
    -var="aws_region=$AWS_REGION" \
    -var="project_name=$PROJECT_NAME" \
    -var="environment=$ENVIRONMENT" \
    -var="budget_limit_usd=$BUDGET_LIMIT_USD" \
    -var="budget_email=$BUDGET_EMAIL"
}

foundation_destroy() {
  tf_foundation destroy -auto-approve \
    -var="aws_region=$AWS_REGION" \
    -var="project_name=$PROJECT_NAME" \
    -var="environment=$ENVIRONMENT" \
    -var="budget_limit_usd=$BUDGET_LIMIT_USD" \
    -var="budget_email=$BUDGET_EMAIL"
}

load_foundation_outputs() {
  VPC_ID="$(tf_foundation output -raw vpc_id)"
  PUBLIC_SUBNET_IDS_JSON="$(tf_foundation output -json public_subnet_ids)"
  ALB_SECURITY_GROUP_ID="$(tf_foundation output -raw alb_security_group_id)"
  API_TASK_SECURITY_GROUP_ID="$(tf_foundation output -raw api_task_security_group_id)"
  WORKER_TASK_SECURITY_GROUP_ID="$(tf_foundation output -raw worker_task_security_group_id)"
  ECS_EXECUTION_ROLE_ARN="$(tf_foundation output -raw ecs_execution_role_arn)"
  ECS_TASK_ROLE_ARN="$(tf_foundation output -raw ecs_task_role_arn)"
  DATABASE_SECRET_ARN="$(tf_foundation output -raw database_secret_arn)"
  DATABASE_HOST="$(tf_foundation output -raw database_host)"
  DATABASE_NAME="$(tf_foundation output -raw database_name)"
  ARTIFACTS_BUCKET_NAME="$(tf_foundation output -raw artifacts_bucket_name)"
  ECR_REPOSITORY_URL="$(tf_foundation output -raw ecr_repository_url)"
  CRAWL_EVENT_QUEUE_URL="$(tf_foundation output -raw crawl_event_queue_url)"
  CRAWL_EVENT_QUEUE_ARN="$(tf_foundation output -raw crawl_event_queue_arn)"

  export VPC_ID PUBLIC_SUBNET_IDS_JSON
  export ALB_SECURITY_GROUP_ID API_TASK_SECURITY_GROUP_ID
  export WORKER_TASK_SECURITY_GROUP_ID
  export ECS_EXECUTION_ROLE_ARN ECS_TASK_ROLE_ARN
  export DATABASE_SECRET_ARN DATABASE_HOST DATABASE_NAME
  export ARTIFACTS_BUCKET_NAME ECR_REPOSITORY_URL
  export CRAWL_EVENT_QUEUE_URL CRAWL_EVENT_QUEUE_ARN
}

write_runtime_vars() {
  local image_uri="$1"
  local api_desired_count="$2"

  export IMAGE_URI="$image_uri"
  export API_DESIRED_COUNT="$api_desired_count"

  python3 - "$RUNTIME/runtime.auto.tfvars.json" <<'PY'
import json
import os
import sys

path = sys.argv[1]
values = {
    "aws_region": os.environ["AWS_REGION"],
    "project_name": os.environ["PROJECT_NAME"],
    "environment": os.environ["ENVIRONMENT"],
    "image_uri": os.environ["IMAGE_URI"],
    "api_desired_count": int(os.environ["API_DESIRED_COUNT"]),
    "vpc_id": os.environ["VPC_ID"],
    "public_subnet_ids": json.loads(os.environ["PUBLIC_SUBNET_IDS_JSON"]),
    "alb_security_group_id": os.environ["ALB_SECURITY_GROUP_ID"],
    "api_task_security_group_id": os.environ["API_TASK_SECURITY_GROUP_ID"],
    "worker_task_security_group_id": os.environ["WORKER_TASK_SECURITY_GROUP_ID"],
    "ecs_execution_role_arn": os.environ["ECS_EXECUTION_ROLE_ARN"],
    "ecs_task_role_arn": os.environ["ECS_TASK_ROLE_ARN"],
    "database_secret_arn": os.environ["DATABASE_SECRET_ARN"],
    "database_host": os.environ["DATABASE_HOST"],
    "database_name": os.environ["DATABASE_NAME"],
    "artifacts_bucket_name": os.environ["ARTIFACTS_BUCKET_NAME"],
    "crawl_event_queue_url": os.environ["CRAWL_EVENT_QUEUE_URL"],
    "crawl_event_queue_arn": os.environ["CRAWL_EVENT_QUEUE_ARN"],
}

with open(path, "w", encoding="utf-8") as handle:
    json.dump(values, handle, indent=2)
    handle.write("\n")
PY
}

task_network_configuration() {
  python3 - <<'PY'
import json
import os

print(json.dumps({
    "awsvpcConfiguration": {
        "subnets": json.loads(os.environ["PUBLIC_SUBNET_IDS_JSON"]),
        "securityGroups": [os.environ["WORKER_TASK_SECURITY_GROUP_ID"]],
        "assignPublicIp": "ENABLED",
    }
}))
PY
}

current_runtime_image_uri() {
  python3 - "$RUNTIME/runtime.auto.tfvars.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["image_uri"])
PY
}
