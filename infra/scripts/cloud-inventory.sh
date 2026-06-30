#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_command aws
export AWS_PAGER=""

load_cloud_env

echo "===== OpenRevive tagged AWS resource inventory ====="
echo "Project:     $PROJECT_NAME"
echo "Environment: $ENVIRONMENT"
echo

aws resourcegroupstaggingapi get-resources \
  --tag-filters \
    "Key=Project,Values=$PROJECT_NAME" \
    "Key=Environment,Values=$ENVIRONMENT" \
  --query 'ResourceTagMappingList[].ResourceARN' \
  --output table
