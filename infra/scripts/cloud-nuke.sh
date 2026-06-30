#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

[[ "${CONFIRM:-}" == "DELETE_DEMO_DATA" ]] || fail \
  "Refusing destructive teardown. Run: CONFIRM=DELETE_DEMO_DATA make cloud-nuke"

load_cloud_env

"$(dirname "$0")/cloud-down.sh" || true

load_foundation_outputs

echo "===== empty artifact bucket ====="
aws s3 rm "s3://${ARTIFACTS_BUCKET_NAME}" --recursive || true

echo "===== destroy foundation and demo data ====="
foundation_destroy

echo "All OpenRevive demo AWS resources were destroyed."
