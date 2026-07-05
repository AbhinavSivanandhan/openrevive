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
    cat > "$CLOUD_ENV" <<'ENVEOF'
AWS_PROFILE=openrevive
AWS_REGION=ap-south-1
PROJECT_NAME=openrevive
ENVIRONMENT=demo
BUDGET_LIMIT_USD=10
BUDGET_EMAIL=
ENVEOF
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

aws_cli() {
  AWS_PAGER="" aws --region "$AWS_REGION" "$@"
}

aws_error_is_not_found() {
  grep -Eqi \
    'ResourceNotFoundException|ClusterNotFoundException|NoSuchEntity|NotFound|does not exist|cannot be found|not found'
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
  BASIC_AUTH_SECRET_ARN="$(tf_foundation output -raw basic_auth_secret_arn)"

  export VPC_ID PUBLIC_SUBNET_IDS_JSON
  export ALB_SECURITY_GROUP_ID API_TASK_SECURITY_GROUP_ID
  export WORKER_TASK_SECURITY_GROUP_ID
  export ECS_EXECUTION_ROLE_ARN ECS_TASK_ROLE_ARN
  export DATABASE_SECRET_ARN DATABASE_HOST DATABASE_NAME
  export ARTIFACTS_BUCKET_NAME ECR_REPOSITORY_URL
  export CRAWL_EVENT_QUEUE_URL CRAWL_EVENT_QUEUE_ARN
  export BASIC_AUTH_SECRET_ARN
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
    "basic_auth_secret_arn": os.environ["BASIC_AUTH_SECRET_ARN"],
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

runtime_cluster_name() {
  printf '%s\n' "${PROJECT_NAME}-${ENVIRONMENT}"
}

runtime_api_service_name() {
  printf '%s\n' "${PROJECT_NAME}-${ENVIRONMENT}-api"
}

runtime_pipe_name() {
  printf '%s\n' "${PROJECT_NAME}-${ENVIRONMENT}-crawl-wakeup"
}

runtime_pipe_role_name() {
  printf '%s\n' "${PROJECT_NAME}-${ENVIRONMENT}-crawl-pipe"
}

runtime_log_group_name() {
  local component="$1"
  printf '%s\n' "/openrevive/${ENVIRONMENT}/${component}"
}

pipe_current_state() {
  local pipe_name="$1"
  local output

  if output="$(aws_cli pipes describe-pipe \
    --name "$pipe_name" \
    --query 'CurrentState' \
    --output text 2>&1)"; then
    printf '%s\n' "$output"
    return 0
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    printf '%s\n' "ABSENT"
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

wait_for_pipe_state() {
  local pipe_name="$1"
  local expected_state="$2"
  local max_attempts="${3:-45}"
  local sleep_seconds="${4:-2}"
  local attempt state

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    state="$(pipe_current_state "$pipe_name")" || return 1

    if [[ "$expected_state" == "ABSENT" && "$state" == "ABSENT" ]]; then
      return 0
    fi

    if [[ "$expected_state" != "ABSENT" && "$state" == "$expected_state" ]]; then
      return 0
    fi

    sleep "$sleep_seconds"
  done

  echo "Timed out waiting for EventBridge Pipe '$pipe_name' to reach $expected_state." >&2
  return 1
}

stop_pipe_if_present() {
  local pipe_name="$1"
  local state

  state="$(pipe_current_state "$pipe_name")" || return 1
  [[ "$state" == "ABSENT" || "$state" == "STOPPED" ]] && return 0

  aws_cli pipes stop-pipe --name "$pipe_name" >/dev/null 2>&1 || true
  wait_for_pipe_state "$pipe_name" "STOPPED"
}

delete_pipe_if_present() {
  local pipe_name="$1"
  local state

  state="$(pipe_current_state "$pipe_name")" || return 1
  [[ "$state" == "ABSENT" ]] && return 0

  stop_pipe_if_present "$pipe_name"
  aws_cli pipes delete-pipe --name "$pipe_name" >/dev/null 2>&1 || true
  wait_for_pipe_state "$pipe_name" "ABSENT"
}

ecs_running_tasks() {
  local cluster_name="$1"
  local output

  if output="$(aws_cli ecs list-tasks \
    --cluster "$cluster_name" \
    --desired-status RUNNING \
    --query 'taskArns[]' \
    --output text 2>&1)"; then
    [[ "$output" != "None" ]] && printf '%s\n' "$output"
    return 0
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

stop_all_ecs_tasks() {
  local cluster_name="$1"
  local tasks task

  tasks="$(ecs_running_tasks "$cluster_name")" || return 1

  for task in $tasks; do
    aws_cli ecs stop-task \
      --cluster "$cluster_name" \
      --task "$task" \
      --reason "OpenRevive teardown" \
      >/dev/null 2>&1 || true
  done
}

wait_for_ecs_tasks_to_stop() {
  local cluster_name="$1"
  local max_attempts="${2:-45}"
  local sleep_seconds="${3:-2}"
  local attempt tasks

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    tasks="$(ecs_running_tasks "$cluster_name")" || return 1
    [[ -z "$tasks" ]] && return 0
    sleep "$sleep_seconds"
  done

  echo "Timed out waiting for ECS tasks in '$cluster_name' to stop." >&2
  return 1
}

ecs_cluster_state() {
  local cluster_name="$1"
  local output

  if output="$(aws_cli ecs describe-clusters \
    --clusters "$cluster_name" \
    --query 'clusters[0].status' \
    --output text 2>&1)"; then
    printf '%s\n' "$output"
    return 0
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    printf '%s\n' "ABSENT"
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

delete_ecs_service_if_present() {
  local cluster_name="$1"
  local service_name="$2"
  local output

  if ! output="$(aws_cli ecs describe-services \
    --cluster "$cluster_name" \
    --services "$service_name" \
    --query 'services[0].status' \
    --output text 2>&1)"; then
    if printf '%s' "$output" | aws_error_is_not_found; then
      return 0
    fi
    printf '%s\n' "$output" >&2
    return 1
  fi

  [[ "$output" == "None" || "$output" == "INACTIVE" ]] && return 0

  aws_cli ecs update-service \
    --cluster "$cluster_name" \
    --service "$service_name" \
    --desired-count 0 \
    >/dev/null 2>&1 || true

  stop_all_ecs_tasks "$cluster_name"
  wait_for_ecs_tasks_to_stop "$cluster_name"

  aws_cli ecs delete-service \
    --cluster "$cluster_name" \
    --service "$service_name" \
    --force \
    >/dev/null 2>&1 || true

  # ECS reports INACTIVE before it fully disappears. Either state is sufficient
  # for cluster deletion and means the service can no longer run tasks.
  local attempt state
  for ((attempt = 1; attempt <= 45; attempt++)); do
    if ! state="$(aws_cli ecs describe-services \
      --cluster "$cluster_name" \
      --services "$service_name" \
      --query 'services[0].status' \
      --output text 2>&1)"; then
      if printf '%s' "$state" | aws_error_is_not_found; then
        return 0
      fi
      printf '%s\n' "$state" >&2
      return 1
    fi

    [[ "$state" == "None" || "$state" == "INACTIVE" ]] && return 0
    sleep 2
  done

  echo "Timed out waiting for ECS service '$service_name' to deactivate." >&2
  return 1
}

delete_ecs_cluster_if_present() {
  local cluster_name="$1"
  local state

  state="$(ecs_cluster_state "$cluster_name")" || return 1
  [[ "$state" == "ABSENT" || "$state" == "INACTIVE" || "$state" == "None" ]] && return 0

  aws_cli ecs delete-cluster --cluster "$cluster_name" >/dev/null 2>&1 || true

  local attempt
  for ((attempt = 1; attempt <= 45; attempt++)); do
    state="$(ecs_cluster_state "$cluster_name")" || return 1
    [[ "$state" == "ABSENT" || "$state" == "INACTIVE" || "$state" == "None" ]] && return 0
    sleep 2
  done

  echo "Timed out waiting for ECS cluster '$cluster_name' to deactivate." >&2
  return 1
}

delete_log_group_if_present() {
  local log_group_name="$1"
  local output

  if output="$(aws_cli logs delete-log-group \
    --log-group-name "$log_group_name" 2>&1)"; then
    return 0
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

delete_pipe_role_if_present() {
  local role_name="$1"
  local policy_name="$2"
  local output attached_policy_arns policy_arn attempt

  aws_cli iam delete-role-policy \
    --role-name "$role_name" \
    --policy-name "$policy_name" \
    >/dev/null 2>&1 || true

  if attached_policy_arns="$(aws iam list-attached-role-policies \
    --role-name "$role_name" \
    --query 'AttachedPolicies[].PolicyArn' \
    --output text 2>&1)"; then
    for policy_arn in $attached_policy_arns; do
      [[ "$policy_arn" == "None" ]] && continue
      aws iam detach-role-policy \
        --role-name "$role_name" \
        --policy-arn "$policy_arn" \
        >/dev/null 2>&1 || true
    done
  elif ! printf '%s' "$attached_policy_arns" | aws_error_is_not_found; then
    printf '%s\n' "$attached_policy_arns" >&2
    return 1
  fi

  for ((attempt = 1; attempt <= 8; attempt++)); do
    if output="$(aws iam delete-role --role-name "$role_name" 2>&1)"; then
      return 0
    fi

    if printf '%s' "$output" | aws_error_is_not_found; then
      return 0
    fi

    sleep "$attempt"
  done

  printf '%s\n' "$output" >&2
  return 1
}

list_task_definition_arns() {
  local family="$1"
  local status="$2"
  local output

  if output="$(aws_cli ecs list-task-definitions \
    --family-prefix "$family" \
    --status "$status" \
    --query 'taskDefinitionArns[]' \
    --output text 2>&1)"; then
    [[ "$output" != "None" ]] && printf '%s\n' "$output"
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

deregister_task_definition_if_present() {
  local arn="$1"
  local output

  if output="$(aws_cli ecs deregister-task-definition \
    --task-definition "$arn" 2>&1)"; then
    return 0
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

delete_task_definition_with_retry() {
  local arn="$1"
  local attempt output

  for ((attempt = 1; attempt <= 8; attempt++)); do
    if output="$(aws_cli ecs delete-task-definitions \
      --task-definitions "$arn" 2>&1)"; then
      # A successful API call returns DELETE_IN_PROGRESS. ECS completes the
      # asynchronous delete after all related tasks and services are gone.
      sleep 1
      return 0
    fi

    if printf '%s' "$output" | aws_error_is_not_found; then
      return 0
    fi

    echo "Retrying task-definition deletion for '$arn' (attempt $attempt/8)." >&2
    sleep "$((attempt * 2))"
  done

  printf '%s\n' "$output" >&2
  return 1
}

wait_for_task_definition_deletion() {
  local arn="$1"
  local attempt output

  for ((attempt = 1; attempt <= 45; attempt++)); do
    if output="$(aws_cli ecs describe-task-definition \
      --task-definition "$arn" \
      --query 'taskDefinition.status' \
      --output text 2>&1)"; then
      if [[ "$output" == "DELETE_IN_PROGRESS" ]]; then
        sleep 2
        continue
      fi

      # A surviving ACTIVE or INACTIVE definition should never be silently
      # accepted after the delete request.
      echo "Task definition '$arn' is still $output." >&2
      sleep 2
      continue
    fi

    if printf '%s' "$output" | aws_error_is_not_found; then
      return 0
    fi

    printf '%s\n' "$output" >&2
    return 1
  done

  echo "Timed out waiting for task definition '$arn' to disappear." >&2
  return 1
}

purge_historical_task_definitions() {
  local temp_file family status arn
  temp_file="$(mktemp)"

  for family in \
    "${PROJECT_NAME}-${ENVIRONMENT}-api" \
    "${PROJECT_NAME}-${ENVIRONMENT}-worker" \
    "${PROJECT_NAME}-${ENVIRONMENT}-migration"; do
    for arn in $(list_task_definition_arns "$family" ACTIVE); do
      [[ "$arn" == "None" ]] && continue
      printf '%s\n' "$arn" >> "$temp_file"
      deregister_task_definition_if_present "$arn"
      sleep 1
    done

    for arn in $(list_task_definition_arns "$family" INACTIVE); do
      [[ "$arn" == "None" ]] && continue
      printf '%s\n' "$arn" >> "$temp_file"
    done
  done

  if [[ ! -s "$temp_file" ]]; then
    rm -f "$temp_file"
    return 0
  fi

  sort -u "$temp_file" -o "$temp_file"

  while IFS= read -r arn; do
    [[ -n "$arn" ]] || continue
    delete_task_definition_with_retry "$arn"
  done < "$temp_file"

  while IFS= read -r arn; do
    [[ -n "$arn" ]] || continue
    wait_for_task_definition_deletion "$arn"
  done < "$temp_file"

  rm -f "$temp_file"
}

remove_runtime_state_entries() {
  local address

  tf_runtime init -input=false >/dev/null

  for address in \
    aws_cloudwatch_log_group.api \
    aws_cloudwatch_log_group.worker \
    aws_cloudwatch_log_group.migration \
    aws_ecs_cluster.main \
    aws_lb.api \
    aws_lb_target_group.api \
    aws_lb_listener.http \
    aws_ecs_task_definition.api \
    aws_ecs_task_definition.worker \
    aws_ecs_task_definition.migration \
    aws_ecs_service.api \
    aws_iam_role.pipe \
    aws_iam_role_policy.pipe \
    aws_pipes_pipe.crawl_wakeup \
    data.aws_iam_policy_document.pipe_assume_role \
    data.aws_secretsmanager_secret.basic_auth; do
    tf_runtime state rm "$address" >/dev/null 2>&1 || true
  done
}

assert_runtime_state_empty() {
  local remaining
  remaining="$(tf_runtime state list 2>/dev/null | grep -v '^data\.' || true)"

  if [[ -n "$remaining" ]]; then
    echo "Runtime Terraform state still contains managed resources:" >&2
    printf '%s\n' "$remaining" >&2
    return 1
  fi
}

assert_runtime_resources_absent() {
  local pipe_name cluster_name role_name log_group component state output remaining
  pipe_name="$(runtime_pipe_name)"
  cluster_name="$(runtime_cluster_name)"
  role_name="$(runtime_pipe_role_name)"

  state="$(pipe_current_state "$pipe_name")" || return 1
  [[ "$state" == "ABSENT" ]] || {
    echo "EventBridge Pipe '$pipe_name' still exists in state $state." >&2
    return 1
  }

  state="$(ecs_cluster_state "$cluster_name")" || return 1
  [[ "$state" == "ABSENT" || "$state" == "INACTIVE" || "$state" == "None" ]] || {
    echo "ECS cluster '$cluster_name' still exists in state $state." >&2
    return 1
  }

  for component in api worker migration; do
    log_group="$(runtime_log_group_name "$component")"
    output="$(aws_cli logs describe-log-groups \
      --log-group-name-prefix "$log_group" \
      --query 'logGroups[].logGroupName' \
      --output text)"
    if [[ -n "$output" && "$output" != "None" ]]; then
      echo "CloudWatch log group '$log_group' still exists." >&2
      return 1
    fi
  done

  if output="$(aws iam get-role --role-name "$role_name" 2>&1)"; then
    echo "Pipe IAM role '$role_name' still exists." >&2
    return 1
  elif ! printf '%s' "$output" | aws_error_is_not_found; then
    printf '%s\n' "$output" >&2
    return 1
  fi

  for component in api worker migration; do
    for state in ACTIVE INACTIVE; do
      remaining="$(list_task_definition_arns \
        "${PROJECT_NAME}-${ENVIRONMENT}-${component}" "$state")"
      if [[ -n "$remaining" ]]; then
        echo "Historical ECS task definitions remain for ${component}: $remaining" >&2
        return 1
      fi
    done
  done
}

assert_aurora_cluster_absent() {
  local cluster_id="${PROJECT_NAME}-${ENVIRONMENT}-aurora"
  local output

  if output="$(aws_cli rds describe-db-clusters \
    --db-cluster-identifier "$cluster_id" 2>&1)"; then
    echo "Aurora cluster '$cluster_id' still exists." >&2
    return 1
  fi

  if printf '%s' "$output" | aws_error_is_not_found; then
    return 0
  fi

  printf '%s\n' "$output" >&2
  return 1
}

assert_foundation_state_empty() {
  local remaining
  remaining="$(tf_foundation state list 2>/dev/null | grep -v '^data\.' || true)"

  if [[ -n "$remaining" ]]; then
    echo "Foundation Terraform state still contains managed resources:" >&2
    printf '%s\n' "$remaining" >&2
    return 1
  fi
}
