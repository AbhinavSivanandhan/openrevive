#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command terraform
require_command aws

load_cloud_env

"$(dirname "$0")/cloud-kill.sh" || true

echo "===== destroy runtime only ====="
tf_runtime destroy -auto-approve

echo "Runtime removed."
echo "Aurora, S3, ECR, SQS, IAM, VPC, and budgets remain."
