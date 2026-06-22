#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script currently supports macOS only."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it first from https://brew.sh"
  exit 1
fi

brew tap hashicorp/tap

if brew help trust >/dev/null 2>&1; then
  brew trust hashicorp/tap || true
fi

brew bundle --file="$PROJECT_ROOT/Brewfile"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env from .env.example"
fi

echo "Bootstrap complete."
echo "Run: ./scripts/verify-dev-env.sh"
