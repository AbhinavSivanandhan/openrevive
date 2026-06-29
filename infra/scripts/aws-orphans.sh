#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

load_deploy_env

echo "===== OpenRevive-tagged resources ====="
aws resourcegroupstaggingapi get-resources \
  --region "$AWS_REGION" \
  --tag-filters Key=Project,Values=openrevive \
  --query 'ResourceTagMappingList[].ResourceARN' \
  --output table

echo
echo "===== Aurora clusters ====="
aws rds describe-db-clusters \
  --region "$AWS_REGION" \
  --query "DBClusters[?contains(DBClusterIdentifier, 'openrevive')].{id:DBClusterIdentifier,status:Status,endpoint:Endpoint}" \
  --output table

echo
echo "===== ECR repositories ====="
aws ecr describe-repositories \
  --region "$AWS_REGION" \
  --query "repositories[?contains(repositoryName, 'openrevive')].{name:repositoryName,uri:repositoryUri}" \
  --output table
