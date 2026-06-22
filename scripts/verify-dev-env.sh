#!/usr/bin/env bash
set -euo pipefail

for tool in git uv node pnpm terraform aws docker jq gitleaks; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "OK   $tool → $(command -v "$tool")"
  else
    echo "MISS $tool"
  fi
done

echo ""
docker compose version || true
