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

# Avoid upgrading unrelated packages during project bootstrap.
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_BUNDLE_NO_UPGRADE=1

# Required by this repository.
brew tap hashicorp/tap

if brew help trust >/dev/null 2>&1; then
  brew trust hashicorp/tap

  # Compatibility for Macs that already have MongoDB Database Tools
  # installed from this external tap. This is not an OpenRevive dependency.
  if brew tap | grep -Fxq "mongodb/brew"; then
    brew trust --formula mongodb/brew/mongodb-database-tools || true
  fi
fi

echo "Installing missing OpenRevive developer tools..."
brew bundle install --file="$PROJECT_ROOT/Brewfile" --no-upgrade

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env from .env.example"
fi

echo "Bootstrap complete."
echo "Run: ./scripts/verify-dev-env.sh"
