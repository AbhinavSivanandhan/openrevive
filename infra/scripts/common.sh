#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_ENV="$ROOT/infra/.local/deploy.env"
RUNTIME_ENV="$ROOT/infra/.local/runtime.env"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

load_deploy_env() {
  if [[ ! -f "$DEPLOY_ENV" ]]; then
    echo "Missing deployment configuration." >&2
    echo "Run: make bootstrap" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "$DEPLOY_ENV"
}

tf_foundation() {
  terraform -chdir="$ROOT/infra/foundation" "$@"
}

tf_runtime() {
  terraform -chdir="$ROOT/infra/runtime" "$@"
}

require_clean_worktree() {
  if [[ "${ALLOW_DIRTY:-0}" == "1" ]]; then
    echo "WARNING: deploying a dirty worktree because ALLOW_DIRTY=1."
    return
  fi

  if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
    echo "Refusing to deploy a dirty Git worktree." >&2
    echo "Commit first, or use ALLOW_DIRTY=1 for an emergency deployment." >&2
    exit 1
  fi
}

image_tag() {
  local sha

  sha="$(git -C "$ROOT" rev-parse --short=12 HEAD)"

  if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
    printf '%s-dirty-%s\n' "$sha" "$(date -u +%Y%m%d%H%M%S)"
    return
  fi

  printf '%s\n' "$sha"
}

foundation_args() {
  FOUNDATION_ARGS=(
    "-var=aws_region=$AWS_REGION"
    "-var=api_domain_name=$API_DOMAIN_NAME"
    "-var=route53_zone_id=$ROUTE53_ZONE_ID"
    "-var=budget_alert_email=$BUDGET_ALERT_EMAIL"
    "-var=monthly_budget_usd=$MONTHLY_BUDGET_USD"
  )
}

load_foundation_outputs() {
  ECR_REPOSITORY_URL="$(tf_foundation output -raw ecr_repository_url)"
  DATABASE_SECRET_ARN="$(tf_foundation output -raw database_secret_arn)"
  DATABASE_HOST="$(tf_foundation output -raw database_host)"
  DATABASE_NAME="$(tf_foundation output -raw database_name)"
  BASIC_AUTH_SECRET_ARN="$(tf_foundation output -raw basic_auth_secret_arn)"
  ARTIFACT_BUCKET_NAME="$(tf_foundation output -raw artifact_bucket_name)"
  API_CERTIFICATE_ARN="$(tf_foundation output -raw api_certificate_arn)"
  VPC_ID="$(tf_foundation output -raw vpc_id)"
  ALB_SECURITY_GROUP_ID="$(tf_foundation output -raw alb_security_group_id)"
  API_TASK_SECURITY_GROUP_ID="$(
    tf_foundation output -raw api_task_security_group_id
  )"
  WORKER_TASK_SECURITY_GROUP_ID="$(
    tf_foundation output -raw worker_task_security_group_id
  )"
  ECS_EXECUTION_ROLE_ARN="$(
    tf_foundation output -raw ecs_execution_role_arn
  )"
  ECS_TASK_ROLE_ARN="$(tf_foundation output -raw ecs_task_role_arn)"

  PUBLIC_SUBNET_IDS_JSON="$(
    tf_foundation output -json public_subnet_ids \
      | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)))'
  )"
}

runtime_args() {
  local api_count="$1"
  local worker_count="$2"
  local tag="$3"

  RUNTIME_ARGS=(
    "-var=aws_region=$AWS_REGION"
    "-var=route53_zone_id=$ROUTE53_ZONE_ID"
    "-var=api_domain_name=$API_DOMAIN_NAME"
    "-var=api_certificate_arn=$API_CERTIFICATE_ARN"
    "-var=vpc_id=$VPC_ID"
    "-var=public_subnet_ids=$PUBLIC_SUBNET_IDS_JSON"
    "-var=alb_security_group_id=$ALB_SECURITY_GROUP_ID"
    "-var=api_task_security_group_id=$API_TASK_SECURITY_GROUP_ID"
    "-var=worker_task_security_group_id=$WORKER_TASK_SECURITY_GROUP_ID"
    "-var=ecr_repository_url=$ECR_REPOSITORY_URL"
    "-var=image_tag=$tag"
    "-var=artifact_bucket_name=$ARTIFACT_BUCKET_NAME"
    "-var=database_secret_arn=$DATABASE_SECRET_ARN"
    "-var=database_host=$DATABASE_HOST"
    "-var=database_name=$DATABASE_NAME"
    "-var=basic_auth_secret_arn=$BASIC_AUTH_SECRET_ARN"
    "-var=ecs_execution_role_arn=$ECS_EXECUTION_ROLE_ARN"
    "-var=ecs_task_role_arn=$ECS_TASK_ROLE_ARN"
    "-var=api_desired_count=$api_count"
    "-var=worker_desired_count=$worker_count"
  )

  if [[ -n "${AUTO_STOP_AT_UTC:-}" ]]; then
    RUNTIME_ARGS+=(
      "-var=auto_stop_at_utc=$AUTO_STOP_AT_UTC"
    )
  fi
}

load_runtime_image_tag() {
  if [[ -f "$RUNTIME_ENV" ]]; then
    # shellcheck disable=SC1090
    source "$RUNTIME_ENV"
    printf '%s\n' "$DEPLOYED_IMAGE_TAG"
    return
  fi

  image_tag
}
