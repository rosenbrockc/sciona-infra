#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_DIR="${1:-$ROOT_DIR/self-hosted}"
UPSTREAM_REPO="${SENTRY_SELF_HOSTED_REPO:-https://github.com/getsentry/self-hosted.git}"
UPSTREAM_REF="${SENTRY_SELF_HOSTED_VERSION:-26.3.1}"

if [[ ! -d "$CHECKOUT_DIR/.git" ]]; then
  git clone "$UPSTREAM_REPO" "$CHECKOUT_DIR"
fi

git -C "$CHECKOUT_DIR" fetch --tags origin
git -C "$CHECKOUT_DIR" checkout "$UPSTREAM_REF"

cp "$ROOT_DIR/docker-compose.override.yml" \
  "$CHECKOUT_DIR/docker-compose.override.yml"

if [[ ! -f "$CHECKOUT_DIR/.env.custom" ]]; then
  cp "$ROOT_DIR/.env.custom.example" "$CHECKOUT_DIR/.env.custom"
fi

echo "Sentry checkout prepared at: $CHECKOUT_DIR"
echo "Next steps:"
echo "  1. Review $CHECKOUT_DIR/.env.custom"
echo "  2. Run: cd $CHECKOUT_DIR && ./install.sh"
echo "  3. Run: cd $CHECKOUT_DIR && docker compose up -d"
