#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env

component="${1:-api}"

case "$component" in
  api)
    group="$(tf_runtime output -raw api_log_group_name)"
    ;;
  worker)
    group="$(tf_runtime output -raw worker_log_group_name)"
    ;;
  *)
    fail "Usage: make cloud-logs COMPONENT=api|worker"
    ;;
esac

aws logs tail "$group" --follow --since 1h
