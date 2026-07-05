#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command terraform
require_command aws

load_cloud_env
tf_runtime init -upgrade

"$(dirname "$0")/cloud-kill.sh" || true

echo "===== destroy runtime only ====="
if ! tf_runtime destroy -auto-approve; then
  echo "Terraform runtime destroy did not complete. Running guarded recovery." >&2
  "$(dirname "$0")/cloud-runtime-recover.sh"
else
  echo "===== purge historical ECS task definitions ====="
  if ! purge_historical_task_definitions; then
    echo "Task-definition cleanup did not complete. Running guarded recovery." >&2
    "$(dirname "$0")/cloud-runtime-recover.sh"
  fi
fi

assert_runtime_resources_absent
assert_runtime_state_empty

echo "Runtime removed."
echo "Aurora, S3, ECR, SQS, IAM, VPC, and budgets remain."
