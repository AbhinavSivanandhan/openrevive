#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_command aws
require_command python3
export AWS_PAGER=""

load_cloud_env

read -r START_DATE END_DATE < <(
  python3 -c '
from datetime import date, timedelta

today = date.today()
month_start = today.replace(day=1)
tomorrow = today + timedelta(days=1)

print(month_start.isoformat(), tomorrow.isoformat())
'
)

echo "===== AWS current-month cost report ====="
echo "Range: $START_DATE through $END_DATE"
echo "Note: Cost Explorer can lag behind actual resource creation."
echo

if aws ce get-cost-and-usage \
  --time-period "Start=$START_DATE,End=$END_DATE" \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --query 'ResultsByTime[0].Groups[].{service:Keys[0],amount:Metrics.UnblendedCost.Amount,unit:Metrics.UnblendedCost.Unit}' \
  --output table; then
  echo
  echo "Cost report completed."
else
  echo
  echo "Cost Explorer has no usable data yet, or access is unavailable."
  echo "This does not indicate a deployment failure."
fi
