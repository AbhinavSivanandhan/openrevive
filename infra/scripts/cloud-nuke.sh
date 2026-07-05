#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

[[ "${CONFIRM:-}" == "DELETE_DEMO_DATA" ]] || fail \
  "Refusing destructive teardown. Run: CONFIRM=DELETE_DEMO_DATA make cloud-nuke"

load_cloud_env

# Runtime must be fully removed before foundation resources, especially the
# foundation-owned Basic Auth secret, are destroyed. Do not continue on a failed
# runtime teardown; cloud-down includes a guarded recovery path when needed.
"$(dirname "$0")/cloud-down.sh"

tf_foundation init -upgrade
load_foundation_outputs

echo "===== empty artifact bucket ====="
aws_cli s3 rm "s3://${ARTIFACTS_BUCKET_NAME}" --recursive || true

echo "===== destroy foundation and demo data ====="
foundation_destroy

echo "===== verify full teardown ====="
assert_aurora_cluster_absent
assert_foundation_state_empty

echo "===== remove local generated deployment artifacts ====="
rm -rf "$INFRA/.local"
rm -f "$RUNTIME/runtime.auto.tfvars.json"
rm -f "$FOUNDATION/terraform.tfstate" "$FOUNDATION/terraform.tfstate.backup"
rm -f "$RUNTIME/terraform.tfstate" "$RUNTIME/terraform.tfstate.backup"

echo "All OpenRevive demo AWS resources and local deployment artifacts were destroyed."
