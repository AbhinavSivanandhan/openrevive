#!/usr/bin/env bash
set -euo pipefail

if [[ "${CONFIRM:-}" != "DELETE_DEMO_DATA" ]]; then
  echo "Refusing destructive teardown." >&2
  echo "Run exactly:" >&2
  echo "  CONFIRM=DELETE_DEMO_DATA make aws-nuke" >&2
  exit 1
fi

source "$(dirname "$0")/common.sh"

load_deploy_env
foundation_args

echo "===== destroy runtime ====="
tf_foundation init
load_foundation_outputs

IMAGE_TAG="$(load_runtime_image_tag)"
runtime_args 0 0 "$IMAGE_TAG"

tf_runtime init
tf_runtime destroy -auto-approve "${RUNTIME_ARGS[@]}" || true

echo "===== enable foundation destruction ====="
tf_foundation apply \
  -auto-approve \
  "${FOUNDATION_ARGS[@]}" \
  "-var=deletion_protection=false" \
  "-var=allow_data_destroy=true"

echo "===== destroy Aurora, artifacts, ECR, secrets, networking, and budget ====="
tf_foundation destroy \
  -auto-approve \
  "${FOUNDATION_ARGS[@]}" \
  "-var=deletion_protection=false" \
  "-var=allow_data_destroy=true"

rm -f "$RUNTIME_ENV"

echo "AWS OpenRevive resources destroyed."
echo "The Vercel project is intentionally retained."
