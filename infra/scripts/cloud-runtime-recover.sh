#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

require_command aws
require_command terraform

load_cloud_env

echo "===== recover partial runtime teardown ====="

PIPE_NAME="$(runtime_pipe_name)"
CLUSTER_NAME="$(runtime_cluster_name)"
API_SERVICE_NAME="$(runtime_api_service_name)"
ROLE_NAME="$(runtime_pipe_role_name)"

# Stop future worker launches before touching ECS. Pipe deletion is asynchronous,
# so wait until it is absent instead of immediately destroying dependent resources.
echo "----- remove EventBridge Pipe -----"
delete_pipe_if_present "$PIPE_NAME"

echo "----- stop and remove ECS service/tasks -----"
delete_ecs_service_if_present "$CLUSTER_NAME" "$API_SERVICE_NAME"
stop_all_ecs_tasks "$CLUSTER_NAME"
wait_for_ecs_tasks_to_stop "$CLUSTER_NAME"

echo "----- remove ECS cluster -----"
delete_ecs_cluster_if_present "$CLUSTER_NAME"

echo "----- remove runtime log groups -----"
for COMPONENT in api worker migration; do
  delete_log_group_if_present "$(runtime_log_group_name "$COMPONENT")"
done

echo "----- remove Pipe IAM role -----"
delete_pipe_role_if_present "$ROLE_NAME" "$ROLE_NAME"

echo "----- purge historical ECS task definitions -----"
purge_historical_task_definitions

echo "----- reconcile local runtime Terraform state -----"
remove_runtime_state_entries

assert_runtime_resources_absent
assert_runtime_state_empty

echo "Recovered runtime teardown: no runtime resources or managed state remain."
