#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

load_deploy_env
foundation_args

tf_foundation init
load_foundation_outputs

IMAGE_TAG="$(load_runtime_image_tag)"
runtime_args 0 0 "$IMAGE_TAG"

echo "Destroying runtime only."
echo "Aurora, S3, ECR, database secret, Basic Auth secret, and foundation networking remain."

tf_runtime init
tf_runtime destroy -auto-approve "${RUNTIME_ARGS[@]}"

rm -f "$RUNTIME_ENV"
