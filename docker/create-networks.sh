#!/usr/bin/env bash
set -euo pipefail

docker network create proxy-tier 2>/dev/null || true
docker network create backend-internal 2>/dev/null || true

echo "Networks ready: proxy-tier, backend-internal"
