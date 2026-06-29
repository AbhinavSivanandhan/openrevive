#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command vercel
load_deploy_env

APP_DIR="$ROOT/apps/web"
API_URL="$(tf_runtime output -raw api_url)"

if ! vercel --cwd "$APP_DIR" project inspect \
  "$VERCEL_PROJECT_NAME" >/dev/null 2>&1; then
  vercel --cwd "$APP_DIR" project add "$VERCEL_PROJECT_NAME"
fi

vercel --cwd "$APP_DIR" link \
  --yes \
  --project "$VERCEL_PROJECT_NAME"

set_vercel_env() {
  local key="$1"
  local value="$2"
  local secret_file

  secret_file="$(mktemp)"
  trap 'rm -f "$secret_file"' RETURN
  chmod 600 "$secret_file"
  printf '%s' "$value" > "$secret_file"

  vercel --cwd "$APP_DIR" env rm \
    "$key" production --yes \
    >/dev/null 2>&1 || true

  vercel --cwd "$APP_DIR" env add \
    "$key" production --sensitive \
    < "$secret_file"

  rm -f "$secret_file"
  trap - RETURN
}

echo "===== configuring Vercel production environment ====="
set_vercel_env "API_INTERNAL_URL" "$API_URL"
set_vercel_env "BASIC_AUTH_ENABLED" "true"
set_vercel_env "BASIC_AUTH_USERNAME" "$BASIC_AUTH_USERNAME"
set_vercel_env "BASIC_AUTH_PASSWORD" "$BASIC_AUTH_PASSWORD"

if [[ -n "$BASIC_AUTH_USERNAME_2" ]]; then
  set_vercel_env "BASIC_AUTH_USERNAME_2" "$BASIC_AUTH_USERNAME_2"
  set_vercel_env "BASIC_AUTH_PASSWORD_2" "$BASIC_AUTH_PASSWORD_2"
else
  vercel --cwd "$APP_DIR" env rm \
    BASIC_AUTH_USERNAME_2 production --yes \
    >/dev/null 2>&1 || true

  vercel --cwd "$APP_DIR" env rm \
    BASIC_AUTH_PASSWORD_2 production --yes \
    >/dev/null 2>&1 || true
fi

echo "===== deploying Vercel production frontend ====="
vercel --cwd "$APP_DIR" deploy --prod --yes
