#!/usr/bin/env bash
set -euo pipefail

echo "=== Phase 0 validation ==="

: "${SUPABASE_URL:?SUPABASE_URL is not set}"
: "${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY is not set}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is not set}"
: "${SUPABASE_DATABASE_URL:?SUPABASE_DATABASE_URL is not set}"

echo "[1/5] Checking Supabase CLI..."
supabase --version

echo "[2/5] Checking REST API connectivity..."
http_status="$(curl -s -o /dev/null -w "%{http_code}" \
  -H "apikey: ${SUPABASE_ANON_KEY}" \
  -H "Authorization: Bearer ${SUPABASE_ANON_KEY}" \
  "${SUPABASE_URL}/rest/v1/")"
if [ "${http_status}" != "200" ]; then
  echo "REST API connectivity failed: ${http_status}" >&2
  exit 1
fi

echo "[3/5] Applying seed data..."
psql "${SUPABASE_DATABASE_URL}" -v ON_ERROR_STOP=1 -f supabase/seed.sql >/dev/null

echo "[4/5] Running validation SQL..."
psql "${SUPABASE_DATABASE_URL}" -v ON_ERROR_STOP=1 -f supabase/sql/phase0_validation.sql

echo "[5/5] Listing migration history..."
supabase migration list

echo "=== Phase 0 validation complete ==="
