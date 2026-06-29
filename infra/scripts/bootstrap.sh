#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

for command in aws terraform docker git python3 vercel; do
  require_command "$command"
done

aws sts get-caller-identity >/dev/null
vercel whoami >/dev/null

read -r -p "AWS region [ap-south-1]: " AWS_REGION
AWS_REGION="${AWS_REGION:-ap-south-1}"

read -r -p "Route 53 hosted zone ID: " ROUTE53_ZONE_ID
ROUTE53_ZONE_ID="${ROUTE53_ZONE_ID#/hostedzone/}"

read -r -p "HTTPS API domain, e.g. api.example.com: " API_DOMAIN_NAME
read -r -p "Budget alert email: " BUDGET_ALERT_EMAIL

read -r -p "Monthly budget in USD [10]: " MONTHLY_BUDGET_USD
MONTHLY_BUDGET_USD="${MONTHLY_BUDGET_USD:-10}"

read -r -p \
  "Optional auto-stop UTC, e.g. 2026-06-30T22:00:00 [blank to skip]: " \
  AUTO_STOP_AT_UTC

read -r -p "Primary Basic Auth username: " BASIC_AUTH_USERNAME
read -r -s -p "Primary Basic Auth password: " BASIC_AUTH_PASSWORD
printf '\n'

if [[ -z "$BASIC_AUTH_USERNAME" || -z "$BASIC_AUTH_PASSWORD" ]]; then
  echo "Primary Basic Auth username and password are required." >&2
  exit 1
fi

read -r -p \
  "Optional second Basic Auth username [blank to skip]: " \
  BASIC_AUTH_USERNAME_2

BASIC_AUTH_PASSWORD_2=""
if [[ -n "$BASIC_AUTH_USERNAME_2" ]]; then
  read -r -s -p "Optional second Basic Auth password: " BASIC_AUTH_PASSWORD_2
  printf '\n'

  if [[ -z "$BASIC_AUTH_PASSWORD_2" ]]; then
    echo "Second username requires a second password." >&2
    exit 1
  fi
fi

read -r -p "Vercel project name [openrevive]: " VERCEL_PROJECT_NAME
VERCEL_PROJECT_NAME="${VERCEL_PROJECT_NAME:-openrevive}"

mkdir -p "$(dirname "$DEPLOY_ENV")"

cat > "$DEPLOY_ENV" <<CONFIG
AWS_REGION=$(printf '%q' "$AWS_REGION")
ROUTE53_ZONE_ID=$(printf '%q' "$ROUTE53_ZONE_ID")
API_DOMAIN_NAME=$(printf '%q' "$API_DOMAIN_NAME")
BUDGET_ALERT_EMAIL=$(printf '%q' "$BUDGET_ALERT_EMAIL")
MONTHLY_BUDGET_USD=$(printf '%q' "$MONTHLY_BUDGET_USD")
AUTO_STOP_AT_UTC=$(printf '%q' "$AUTO_STOP_AT_UTC")
BASIC_AUTH_USERNAME=$(printf '%q' "$BASIC_AUTH_USERNAME")
BASIC_AUTH_PASSWORD=$(printf '%q' "$BASIC_AUTH_PASSWORD")
BASIC_AUTH_USERNAME_2=$(printf '%q' "$BASIC_AUTH_USERNAME_2")
BASIC_AUTH_PASSWORD_2=$(printf '%q' "$BASIC_AUTH_PASSWORD_2")
VERCEL_PROJECT_NAME=$(printf '%q' "$VERCEL_PROJECT_NAME")
CONFIG

chmod 600 "$DEPLOY_ENV"

echo
echo "Created ignored deployment configuration:"
echo "  $DEPLOY_ENV"
echo
echo "Next step after Part 3B:"
echo "  make bootstrap"
