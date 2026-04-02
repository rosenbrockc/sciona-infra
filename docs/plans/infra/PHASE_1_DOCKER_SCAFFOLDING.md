# Phase 1 — Docker Scaffolding: Agent-Executable Implementation Plan

**Parent plan:** `docs/plans/infra/README.md` (Phase 1 section)
**Goal:** All compose stacks pass `docker compose config` and can start with
`docker compose up -d` once external networks exist and `.env` is populated.

---

## Current State Summary

| Subdir | File | Issues |
|---|---|---|
| `proxy/` | `compose.yml` | Hardcoded `yourdomain.com`, no healthchecks |
| `authentik/` | `compose.yml` | Hardcoded secrets (`authentik_db_password`, `generate_a_long_secret_key_here`), hardcoded `yourdomain.com`, no healthchecks |
| `temporal/` | `compose.yml` | Hardcoded secrets (`temporal_password`), hardcoded `yourdomain.com`, no healthchecks, references missing config file `config/dynamicconfig/development-sql.yaml` |
| `opa/` | `compose.yml` | Uses `latest-rootless` tag (unpinned), no healthcheck, `./policies` dir does not exist |
| `telemetry/` | `compos.yml` | **Filename typo** (missing `e`), references missing `otelcol-config.yaml`, no healthcheck |
| `sentry/` | *(empty dir)* | No files at all |

---

## Step-by-Step Tasks

### Task 1: Create `docker/create-networks.sh`

**Action:** Create new file.

**File:** `docker/create-networks.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Create external Docker networks required by the compose stacks.
# Idempotent — safe to run multiple times.

docker network create proxy-tier 2>/dev/null || true
docker network create backend-internal 2>/dev/null || true

echo "Networks ready: proxy-tier, backend-internal"
```

**Post-action:** `chmod +x docker/create-networks.sh`

---

### Task 2: Create `docker/.env.example`

**Action:** Create new file. This is the single source of truth for every
variable referenced by `${VAR}` in the compose files after Task 5 edits.

**File:** `docker/.env.example`

```dotenv
# =============================================================================
# docker/.env.example — Copy to .env and fill in real values
# =============================================================================

# --- Domain ------------------------------------------------------------------
DOMAIN=yourdomain.com

# --- Proxy / ACME ------------------------------------------------------------
ACME_EMAIL=admin@${DOMAIN}

# --- Authentik ----------------------------------------------------------------
AUTHENTIK_PG_PASSWORD=change-me-authentik-pg
AUTHENTIK_SECRET_KEY=change-me-generate-64-char-random-string

# --- Temporal -----------------------------------------------------------------
TEMPORAL_PG_PASSWORD=change-me-temporal-pg

# --- Sentry (SaaS) -----------------------------------------------------------
# Obtain from https://sentry.io → Project Settings → Client Keys (DSN)
SENTRY_DSN=
```

---

### Task 3: Rename the telemetry compose file (fix typo)

**Action:** Rename `docker/telemetry/compos.yml` to `docker/telemetry/compose.yml`.

**Command:**
```bash
mv docker/telemetry/compos.yml docker/telemetry/compose.yml
```

---

### Task 4: Create missing config files

#### 4a. `docker/telemetry/otelcol-config.yaml`

**Action:** Create new file.

**File:** `docker/telemetry/otelcol-config.yaml`

```yaml
# OpenTelemetry Collector configuration
# Receives OTLP from application services, batches, and exports.

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  batch:
    send_batch_size: 1024
    timeout: 5s

exporters:
  # Console logging for local development
  logging:
    verbosity: basic
    sampling_initial: 5
    sampling_thereafter: 200

  # Uncomment and configure when a backend (Jaeger, Grafana Tempo, etc.) is added:
  # otlp/backend:
  #   endpoint: "tempo:4317"
  #   tls:
  #     insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
```

#### 4b. `docker/opa/policies/main.rego`

**Action:** Create directory `docker/opa/policies/` and file.

**Commands:**
```bash
mkdir -p docker/opa/policies
```

**File:** `docker/opa/policies/main.rego`

```rego
package system

# Stub root policy. Individual domain policies (bounty, payout, etc.)
# will be added in Phase 4. This file exists so OPA has at least one
# valid policy to load at startup.

default main = true
```

#### 4c. `docker/temporal/config/dynamicconfig/development-sql.yaml`

**Action:** Create directory tree and file.

**Commands:**
```bash
mkdir -p docker/temporal/config/dynamicconfig
```

**File:** `docker/temporal/config/dynamicconfig/development-sql.yaml`

```yaml
# Temporal dynamic configuration overrides for development.
# Empty map means "use all defaults."
# See https://docs.temporal.io/references/dynamic-configuration
{}
```

---

### Task 5: Modify each compose file

All edits below use the `Edit` tool (exact old/new string replacements).
After all edits, every compose file must pass `docker compose config` when
a `.env` file exists with values for every variable.

#### 5a. `docker/proxy/compose.yml`

**Edit 1 — Remove deprecated `version` key:**

- old_string: `version: '3.8'\n\nservices:`
- new_string: `services:`

**Edit 2 — Replace hardcoded email with env var:**

- old_string: `- DEFAULT_EMAIL=admin@yourdomain.com # Update this`
- new_string: `- DEFAULT_EMAIL=${ACME_EMAIL}`

**Edit 3 — Add healthcheck to nginx-proxy:**

- old_string (locate the anchor):
```
    networks:
      - proxy-tier

  acme-companion:
```
- new_string:
```
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost/ || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - proxy-tier

  acme-companion:
```

**Edit 4 — Add healthcheck to acme-companion:**

- old_string (locate anchor):
```
    networks:
      - proxy-tier

volumes:
```
- new_string:
```
    healthcheck:
      test: ["CMD-SHELL", "test -f /etc/acme.sh/acme.sh"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 30s
    networks:
      - proxy-tier

volumes:
```

**Full expected result after edits — `docker/proxy/compose.yml`:**

```yaml
services:
  nginx-proxy:
    image: nginxproxy/nginx-proxy:alpine
    container_name: nginx-proxy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - conf:/etc/nginx/conf.d
      - vhost:/etc/nginx/vhost.d
      - html:/usr/share/nginx/html
      - certs:/etc/nginx/certs:ro
      - /var/run/docker.sock:/tmp/docker.sock:ro
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost/ || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - proxy-tier

  acme-companion:
    image: nginxproxy/acme-companion
    container_name: nginx-acme-companion
    restart: always
    environment:
      - DEFAULT_EMAIL=${ACME_EMAIL}
    volumes_from:
      - nginx-proxy
    volumes:
      - certs:/etc/nginx/certs:rw
      - acme:/etc/acme.sh
      - /var/run/docker.sock:/var/run/docker.sock:ro
    healthcheck:
      test: ["CMD-SHELL", "test -f /etc/acme.sh/acme.sh"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 30s
    networks:
      - proxy-tier

volumes:
  conf:
  vhost:
  html:
  certs:
  acme:

networks:
  proxy-tier:
    external: true
```

---

#### 5b. `docker/authentik/compose.yml`

**Edit 1 — Remove deprecated `version` key:**

- old_string: `version: '3.8'\n\nservices:`
- new_string: `services:`

**Edit 2 — Extract postgres password (postgresql service):**

- old_string: `- POSTGRES_PASSWORD=authentik_db_password`
- new_string: `- POSTGRES_PASSWORD=${AUTHENTIK_PG_PASSWORD}`

**Edit 3 — Extract secrets & domain (server service).** Replace the entire environment block:

- old_string:
```
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=authentik_db_password
      - AUTHENTIK_SECRET_KEY=generate_a_long_secret_key_here
      - VIRTUAL_HOST=auth.yourdomain.com
      - LETSENCRYPT_HOST=auth.yourdomain.com
      - VIRTUAL_PORT=9000
    depends_on:
      - postgresql
      - redis
    volumes:
      - authentik-media:/media
      - authentik-custom-templates:/templates
    networks:
      - authentik-internal
      - proxy-tier

  worker:
```
- new_string:
```
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
      - VIRTUAL_HOST=auth.${DOMAIN}
      - LETSENCRYPT_HOST=auth.${DOMAIN}
      - VIRTUAL_PORT=9000
    depends_on:
      postgresql:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - authentik-media:/media
      - authentik-custom-templates:/templates
    healthcheck:
      test: ["CMD", "ak", "healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    networks:
      - authentik-internal
      - proxy-tier

  worker:
```

**Edit 4 — Extract secrets (worker service):**

- old_string:
```
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=authentik_db_password
      - AUTHENTIK_SECRET_KEY=generate_a_long_secret_key_here
    depends_on:
      - postgresql
      - redis
```
- new_string:
```
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
    depends_on:
      postgresql:
        condition: service_healthy
      redis:
        condition: service_healthy
```

**Edit 5 — Add healthchecks to postgresql and redis:**

- old_string:
```
  postgresql:
    image: postgres:15-alpine
    container_name: authentik-postgres
    restart: always
    environment:
      - POSTGRES_PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - POSTGRES_USER=authentik
      - POSTGRES_DB=authentik
    volumes:
      - authentik-db-data:/var/lib/postgresql/data
    networks:
      - authentik-internal

  redis:
    image: redis:alpine
    container_name: authentik-redis
    restart: always
    volumes:
      - authentik-redis-data:/data
    networks:
      - authentik-internal
```
- new_string:
```
  postgresql:
    image: postgres:15-alpine
    container_name: authentik-postgres
    restart: always
    environment:
      - POSTGRES_PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - POSTGRES_USER=authentik
      - POSTGRES_DB=authentik
    volumes:
      - authentik-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U authentik"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - authentik-internal

  redis:
    image: redis:alpine
    container_name: authentik-redis
    restart: always
    volumes:
      - authentik-redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - authentik-internal
```

**Full expected result after all edits — `docker/authentik/compose.yml`:**

```yaml
services:
  postgresql:
    image: postgres:15-alpine
    container_name: authentik-postgres
    restart: always
    environment:
      - POSTGRES_PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - POSTGRES_USER=authentik
      - POSTGRES_DB=authentik
    volumes:
      - authentik-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U authentik"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - authentik-internal

  redis:
    image: redis:alpine
    container_name: authentik-redis
    restart: always
    volumes:
      - authentik-redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - authentik-internal

  server:
    image: ghcr.io/goauthentik/server:2024.2.2
    container_name: authentik-server
    restart: always
    command: server
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
      - VIRTUAL_HOST=auth.${DOMAIN}
      - LETSENCRYPT_HOST=auth.${DOMAIN}
      - VIRTUAL_PORT=9000
    depends_on:
      postgresql:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - authentik-media:/media
      - authentik-custom-templates:/templates
    healthcheck:
      test: ["CMD", "ak", "healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    networks:
      - authentik-internal
      - proxy-tier

  worker:
    image: ghcr.io/goauthentik/server:2024.2.2
    container_name: authentik-worker
    restart: always
    command: worker
    environment:
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=${AUTHENTIK_PG_PASSWORD}
      - AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY}
    depends_on:
      postgresql:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - authentik-media:/media
      - authentik-custom-templates:/templates
    networks:
      - authentik-internal

volumes:
  authentik-db-data:
  authentik-redis-data:
  authentik-media:
  authentik-custom-templates:

networks:
  authentik-internal:
    internal: true
  proxy-tier:
    external: true
```

---

#### 5c. `docker/temporal/compose.yml`

**Edit 1 — Remove deprecated `version` key:**

- old_string: `version: '3.8'\n\nservices:`
- new_string: `services:`

**Edit 2 — Extract postgres password and add healthcheck (postgresql):**

- old_string:
```
  postgresql:
    image: postgres:14-alpine
    container_name: temporal-postgres
    restart: always
    environment:
      - POSTGRES_USER=temporal
      - POSTGRES_PASSWORD=temporal_password
    volumes:
      - temporal-db-data:/var/lib/postgresql/data
    networks:
      - temporal-internal
```
- new_string:
```
  postgresql:
    image: postgres:14-alpine
    container_name: temporal-postgres
    restart: always
    environment:
      - POSTGRES_USER=temporal
      - POSTGRES_PASSWORD=${TEMPORAL_PG_PASSWORD}
    volumes:
      - temporal-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - temporal-internal
```

**Edit 3 — Extract secret, mount config, add healthcheck (temporal server):**

- old_string:
```
  temporal:
    image: temporalio/auto-setup:1.22.4
    container_name: temporal-server
    restart: always
    depends_on:
      - postgresql
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal_password
      - POSTGRES_SEEDS=postgresql
      - DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml
    networks:
      - temporal-internal
      - proxy-tier # Only needed if workers connect from outside, otherwise keep internal
```
- new_string:
```
  temporal:
    image: temporalio/auto-setup:1.22.4
    container_name: temporal-server
    restart: always
    depends_on:
      postgresql:
        condition: service_healthy
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=${TEMPORAL_PG_PASSWORD}
      - POSTGRES_SEEDS=postgresql
      - DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml
    volumes:
      - ./config/dynamicconfig:/etc/temporal/config/dynamicconfig:ro
    healthcheck:
      test: ["CMD", "temporal", "operator", "cluster", "health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 40s
    networks:
      - temporal-internal
      - backend-internal
```

**Edit 4 — Replace hardcoded domain in temporal-ui, add healthcheck:**

- old_string:
```
  temporal-ui:
    image: temporalio/ui:2.22.2
    container_name: temporal-ui
    restart: always
    depends_on:
      - temporal
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
      - TEMPORAL_CORS_ORIGINS=http://localhost:3000
      - VIRTUAL_HOST=temporal.yourdomain.com
      - LETSENCRYPT_HOST=temporal.yourdomain.com
      - VIRTUAL_PORT=8080
    networks:
      - temporal-internal
      - proxy-tier
```
- new_string:
```
  temporal-ui:
    image: temporalio/ui:2.22.2
    container_name: temporal-ui
    restart: always
    depends_on:
      temporal:
        condition: service_healthy
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
      - TEMPORAL_CORS_ORIGINS=http://localhost:3000
      - VIRTUAL_HOST=temporal.${DOMAIN}
      - LETSENCRYPT_HOST=temporal.${DOMAIN}
      - VIRTUAL_PORT=8080
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8080 || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
    networks:
      - temporal-internal
      - proxy-tier
```

**Edit 5 — Add `backend-internal` network declaration:**

- old_string:
```
networks:
  temporal-internal:
    internal: true
  proxy-tier:
    external: true
```
- new_string:
```
networks:
  temporal-internal:
    internal: true
  proxy-tier:
    external: true
  backend-internal:
    external: true
```

**Full expected result after all edits — `docker/temporal/compose.yml`:**

```yaml
services:
  postgresql:
    image: postgres:14-alpine
    container_name: temporal-postgres
    restart: always
    environment:
      - POSTGRES_USER=temporal
      - POSTGRES_PASSWORD=${TEMPORAL_PG_PASSWORD}
    volumes:
      - temporal-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U temporal"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - temporal-internal

  temporal:
    image: temporalio/auto-setup:1.22.4
    container_name: temporal-server
    restart: always
    depends_on:
      postgresql:
        condition: service_healthy
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=${TEMPORAL_PG_PASSWORD}
      - POSTGRES_SEEDS=postgresql
      - DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml
    volumes:
      - ./config/dynamicconfig:/etc/temporal/config/dynamicconfig:ro
    healthcheck:
      test: ["CMD", "temporal", "operator", "cluster", "health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 40s
    networks:
      - temporal-internal
      - backend-internal

  temporal-ui:
    image: temporalio/ui:2.22.2
    container_name: temporal-ui
    restart: always
    depends_on:
      temporal:
        condition: service_healthy
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
      - TEMPORAL_CORS_ORIGINS=http://localhost:3000
      - VIRTUAL_HOST=temporal.${DOMAIN}
      - LETSENCRYPT_HOST=temporal.${DOMAIN}
      - VIRTUAL_PORT=8080
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8080 || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
    networks:
      - temporal-internal
      - proxy-tier

volumes:
  temporal-db-data:

networks:
  temporal-internal:
    internal: true
  proxy-tier:
    external: true
  backend-internal:
    external: true
```

**Note:** The `temporal` service network changed from `proxy-tier` to
`backend-internal`. Application workers connect to Temporal over
`backend-internal`; only `temporal-ui` needs proxy exposure. This is a
deliberate security improvement.

---

#### 5d. `docker/opa/compose.yml`

**Action:** Replace the entire file content. Changes: remove `version` key,
pin OPA version to `0.67.0-rootless`, add healthcheck, add `--addr` flag.

**Write full file — `docker/opa/compose.yml`:**

```yaml
services:
  opa:
    image: openpolicyagent/opa:0.67.0-rootless
    container_name: sciona-opa
    restart: always
    command:
      - "run"
      - "--server"
      - "--addr=0.0.0.0:8181"
      - "--log-level=info"
      - "/policies"
    volumes:
      - ./policies:/policies:ro
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8181/health || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 5s
    networks:
      - backend-internal

networks:
  backend-internal:
    external: true
```

---

#### 5e. `docker/telemetry/compose.yml` (after rename in Task 3)

**Edit 1 — Remove deprecated `version` key:**

- old_string: `version: '3.8'\n\nservices:`
- new_string: `services:`

**Edit 2 — Add healthcheck:**

- old_string:
```
    ports:
      - "4317:4317" # OTLP gRPC receiver
      - "4318:4318" # OTLP HTTP receiver
    networks:
      - backend-internal
```
- new_string:
```
    ports:
      - "4317:4317" # OTLP gRPC receiver
      - "4318:4318" # OTLP HTTP receiver
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:13133/ || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - backend-internal
```

**Edit 3 — Add health extension to otelcol-config.yaml** (amend the config
created in Task 4a). Append after the `service:` block:

Actually, this requires modifying the otelcol config. Update the file created
in Task 4a to include the health_check extension. The final content for
`docker/telemetry/otelcol-config.yaml` should be as specified in Task 4a but
with this addition under `service:`:

```yaml
  extensions: [health_check]

extensions:
  health_check:
    endpoint: "0.0.0.0:13133"
```

**Revised full content for `docker/telemetry/otelcol-config.yaml`:**

```yaml
# OpenTelemetry Collector configuration
# Receives OTLP from application services, batches, and exports.

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  batch:
    send_batch_size: 1024
    timeout: 5s

exporters:
  # Console logging for local development
  logging:
    verbosity: basic
    sampling_initial: 5
    sampling_thereafter: 200

  # Uncomment and configure when a backend (Jaeger, Grafana Tempo, etc.) is added:
  # otlp/backend:
  #   endpoint: "tempo:4317"
  #   tls:
  #     insecure: true

extensions:
  health_check:
    endpoint: "0.0.0.0:13133"

service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
```

**Full expected result after edits — `docker/telemetry/compose.yml`:**

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.96.0
    container_name: otel-collector
    restart: always
    command: ["--config=/etc/otelcol-config.yaml"]
    volumes:
      - ./otelcol-config.yaml:/etc/otelcol-config.yaml:ro
    ports:
      - "4317:4317" # OTLP gRPC receiver
      - "4318:4318" # OTLP HTTP receiver
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:13133/ || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - backend-internal

networks:
  backend-internal:
    external: true
```

---

### Task 6: Sentry — SaaS approach (no self-hosted compose)

**Decision:** Follow the parent plan recommendation. Use Sentry SaaS (free
tier, 50k events/month) with the Python SDK only. Do **not** create a
`docker/sentry/compose.yml`.

**Action:** Create a placeholder README in the sentry directory explaining the
decision.

**File:** `docker/sentry/README.md`

```
# Sentry — SaaS Mode

This project uses Sentry SaaS (https://sentry.io) instead of self-hosting.

## Setup

1. Create a Sentry project at https://sentry.io
2. Copy the DSN to `docker/.env` as `SENTRY_DSN=https://...@sentry.io/...`
3. The Python SDK (`sentry-sdk[fastapi]`) reads this DSN at startup

## When to self-host

If event volume exceeds the SaaS free tier (50k errors/month), revisit
self-hosting with `getsentry/self-hosted`. The compose slot in this
directory is reserved for that migration.
```

---

### Task 7: Create `docker/README.md`

**Action:** Create new file.

**File:** `docker/README.md`

```markdown
# Docker Infrastructure

## Networks

Two external Docker networks must exist before any stack starts:

| Network | Purpose |
|---|---|
| `proxy-tier` | Connects nginx-proxy to any service that needs HTTPS exposure |
| `backend-internal` | Connects application services (API, workers) to OPA, Temporal, OTel collector |

Create them:

```bash
./create-networks.sh
```

## Environment

Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env
$EDITOR .env
```

Every compose file reads variables from `../.env` (the docker root).
When running from a subdirectory, pass `--env-file ../. env`:

```bash
cd proxy && docker compose --env-file ../.env config
```

## Boot Order

Services have the following dependency chain:

```
1. Networks          (create-networks.sh)
2. proxy             (nginx-proxy + acme-companion)
3. telemetry         (OTel collector — no deps, but start early so traces aren't lost)
4. opa               (no deps beyond network)
5. temporal           (internal postgres → temporal server → temporal-ui)
6. authentik         (internal postgres + redis → server + worker)
```

Steps 3, 4, 5, 6 can run in parallel once proxy and networks are up.

### Quick start (all stacks)

```bash
./create-networks.sh
cp .env.example .env  # then edit

for dir in proxy telemetry opa temporal authentik; do
  (cd "$dir" && docker compose --env-file ../.env up -d)
done
```

### Tear down

```bash
for dir in authentik temporal opa telemetry proxy; do
  (cd "$dir" && docker compose --env-file ../.env down)
done
```

## Subdirectory Index

| Directory | Services | External Networks |
|---|---|---|
| `proxy/` | nginx-proxy, acme-companion | `proxy-tier` |
| `authentik/` | postgres, redis, authentik-server, authentik-worker | `proxy-tier`, (internal) |
| `temporal/` | postgres, temporal-server, temporal-ui | `proxy-tier`, `backend-internal`, (internal) |
| `opa/` | opa | `backend-internal` |
| `telemetry/` | otel-collector | `backend-internal` |
| `sentry/` | *(SaaS — no containers)* | — |

## Sentry

Sentry uses SaaS mode. See `sentry/README.md`.
```

---

## Execution Order for the Agent

Run these tasks in this exact sequence:

1. **Task 2** — Create `docker/.env.example`
2. **Task 1** — Create `docker/create-networks.sh` and `chmod +x`
3. **Task 3** — `mv docker/telemetry/compos.yml docker/telemetry/compose.yml`
4. **Task 4a** — Create `docker/telemetry/otelcol-config.yaml` (revised version with health_check extension)
5. **Task 4b** — `mkdir -p docker/opa/policies` then create `docker/opa/policies/main.rego`
6. **Task 4c** — `mkdir -p docker/temporal/config/dynamicconfig` then create `development-sql.yaml`
7. **Task 5a** — Edit `docker/proxy/compose.yml` (4 edits)
8. **Task 5b** — Edit `docker/authentik/compose.yml` (5 edits)
9. **Task 5c** — Edit `docker/temporal/compose.yml` (5 edits)
10. **Task 5d** — Write full `docker/opa/compose.yml`
11. **Task 5e** — Edit `docker/telemetry/compose.yml` (2 edits)
12. **Task 6** — Create `docker/sentry/README.md`
13. **Task 7** — Create `docker/README.md`

---

## Verification

After all tasks, run in each subdirectory (skip `sentry/`):

```bash
cd /path/to/docker

# Create a real .env from the example
cp .env.example .env
# Fill dummy values for validation
sed -i '' \
  -e 's/change-me-authentik-pg/testpass/' \
  -e 's/change-me-generate-64-char-random-string/testsecretkey1234567890123456789012345678901234567890/' \
  -e 's/change-me-temporal-pg/testpass/' \
  -e 's/yourdomain.com/example.com/' \
  .env

for dir in proxy authentik temporal opa telemetry; do
  echo "=== $dir ==="
  (cd "$dir" && docker compose --env-file ../.env config > /dev/null && echo "OK") || echo "FAIL"
done
```

**Expected output:**

```
=== proxy ===
OK
=== authentik ===
OK
=== temporal ===
OK
=== opa ===
OK
=== telemetry ===
OK
```

If any stack prints `FAIL`, the error message from `docker compose config`
will indicate the specific issue (missing variable, YAML syntax, etc.).

---

## Files Summary

| Action | File |
|---|---|
| Create | `docker/create-networks.sh` |
| Create | `docker/.env.example` |
| Rename | `docker/telemetry/compos.yml` → `docker/telemetry/compose.yml` |
| Create | `docker/telemetry/otelcol-config.yaml` |
| Create | `docker/opa/policies/main.rego` |
| Create | `docker/temporal/config/dynamicconfig/development-sql.yaml` |
| Create | `docker/sentry/README.md` |
| Create | `docker/README.md` |
| Modify | `docker/proxy/compose.yml` |
| Modify | `docker/authentik/compose.yml` |
| Modify | `docker/temporal/compose.yml` |
| Write  | `docker/opa/compose.yml` (full replacement) |
| Modify | `docker/telemetry/compose.yml` |
