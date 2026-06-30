#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_command aws
require_command python3

load_cloud_env

CREDENTIALS_FILE="$INFRA/.local/basic-auth.json"

mkdir -p "$INFRA/.local"
chmod 700 "$INFRA/.local"

if [[ ! -f "$CREDENTIALS_FILE" ]]; then
  umask 077

  python3 - "$CREDENTIALS_FILE" <<'PY'
import json
import secrets
import sys

path = sys.argv[1]

with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "username": "openrevive",
            "password": secrets.token_urlsafe(32),
        },
        handle,
    )
    handle.write("\n")
PY

  chmod 600 "$CREDENTIALS_FILE"
  echo "Created local private-access credentials."
fi

python3 - "$CREDENTIALS_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

username = payload.get("username")
password = payload.get("password")

if not isinstance(username, str) or not username.strip():
    raise SystemExit("Credential file has no valid username.")

if not isinstance(password, str) or len(password) < 24:
    raise SystemExit("Credential file has no valid password.")
PY

SECRET_ARN="$(
  terraform -chdir="$INFRA/foundation" output -raw basic_auth_secret_arn
)"

aws secretsmanager put-secret-value \
  --secret-id "$SECRET_ARN" \
  --secret-string "file://$CREDENTIALS_FILE" \
  >/dev/null

echo "Private-access secret is ready."
