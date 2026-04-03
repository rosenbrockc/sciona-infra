#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-$ROOT_DIR/.env}"

rand_hex() {
  local bytes="$1"
  openssl rand -hex "$bytes"
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local value
  read -r -p "$prompt [$default_value]: " value
  if [[ -z "$value" ]]; then
    value="$default_value"
  fi
  printf '%s' "$value"
}

write_if_missing() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

default_domain="yourdomain.com"
domain="$(prompt_with_default "Domain" "$default_domain")"
default_email="admin@${domain}"
acme_email="$(prompt_with_default "ACME email" "$default_email")"

write_if_missing DOMAIN "$domain"
write_if_missing ACME_EMAIL "$acme_email"
write_if_missing AUTHENTIK_PG_PASSWORD "$(rand_hex 24)"
write_if_missing AUTHENTIK_SECRET_KEY "$(rand_hex 32)"
write_if_missing TEMPORAL_PG_PASSWORD "$(rand_hex 24)"
write_if_missing SENTRY_HOSTNAME "sentry.${domain}"
write_if_missing SENTRY_SELF_HOSTED_VERSION 26.3.1
write_if_missing SENTRY_DSN ""

echo "Wrote secrets to $ENV_FILE"
