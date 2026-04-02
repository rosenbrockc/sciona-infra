# Infrastructure Integration Plan — Self-Hosted OSS Stack

## Context

The gap analysis (Section 2) recommends self-hosting Temporal, OPA, Authentik,
Sentry + OpenTelemetry behind an nginx reverse proxy.  Docker compose files
exist in `docker/` but have issues.  The FastAPI backend and React frontend
already exist and need to be wired into these services.

This plan covers: (1) fixing the compose scaffolding, (2) integrating each
service into the existing codebase, (3) updating the frontend where needed.

---

## Current State

### What exists

| Layer | Location | Status |
|---|---|---|
| Frontend | `frontend/` (React/Vite/Tailwind, 8 pages) | Functional with mock data, calls FastAPI |
| Backend API | `sciona/api/` (FastAPI, 6 routers) | Functional, uses Supabase + Memgraph |
| Auth | `sciona/api/routers/auth.py` | GitHub OAuth via Supabase + device flow |
| Bounty lifecycle | `sciona/api/routers/bounty.py` + `bounty_state.py` | Inline state machine, no durable orchestration |
| Settlement | `sciona/clearinghouse/settlement.py` | Pure computation, not wrapped in retry/workflow |
| Verification | `sciona/api/routers/verification.py` | Polling endpoints, no workflow status integration |
| Database | `supabase/` (PostgreSQL + migrations) | Local dev via Supabase CLI |
| Graph DB | Root `docker-compose.yml` (Memgraph) | Local dev only |
| Docker infra | `docker/` (6 subdirs) | Scaffolded, multiple issues |

### What's missing

- No Temporal client or workflow definitions anywhere in the codebase
- No OPA policy files or policy evaluation calls
- No OpenTelemetry instrumentation (no spans, no trace context)
- No Sentry SDK integration
- No unified docker orchestration (external networks not created)
- Authentik exists in compose but isn't wired to Supabase or the API
- Compose files have hardcoded secrets, missing configs, filename typo

---

## Phase 1 — Fix Docker Scaffolding

**Goal:** All 6 compose stacks start cleanly with `docker compose up -d`.

### 1a. Create shared infrastructure

**File:** `docker/README.md` (create)

Document the network creation order and environment setup.

**File:** `docker/create-networks.sh` (create)

```bash
#!/bin/bash
docker network create proxy-tier 2>/dev/null || true
docker network create backend-internal 2>/dev/null || true
```

### 1b. Fix compose issues

| File | Fix |
|---|---|
| `docker/telemetry/compos.yml` | Rename to `compose.yml` |
| `docker/telemetry/otelcol-config.yaml` | Create with receivers (otlp), processors (batch), exporters (logging, otlp/sentry) |
| `docker/opa/policies/` | Create directory with a minimal `main.rego` stub |
| `docker/temporal/config/dynamicconfig/development-sql.yaml` | Create with empty overrides `{}` |
| All compose files | Extract secrets to `.env.example` files per directory |
| `docker/opa/compose.yml` | Pin OPA image version (e.g., `0.67.0-rootless`) |
| All compose files | Add `healthcheck` blocks |
| All compose files | Replace `yourdomain.com` with `${DOMAIN}` variable |

### 1c. Create Sentry compose

**File:** `docker/sentry/compose.yml` (create)

Use the official `getsentry/self-hosted` approach or the lighter Sentry
relay + Kafka + Snuba stack.  For initial scaffolding, use the relay-only
mode that forwards to a Sentry SaaS DSN (hybrid approach — self-hosted
collector, cloud storage).  This avoids the heavy 10+ container self-hosted
Sentry stack for now.

```yaml
services:
  sentry-relay:
    image: getsentry/relay:latest
    volumes:
      - ./relay-config.yml:/etc/relay/config.yml:ro
    networks:
      - backend-internal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/api/relay/healthcheck/ready/"]
      interval: 30s
      timeout: 5s
      retries: 3

networks:
  backend-internal:
    external: true
```

Alternative: skip self-hosted Sentry entirely for now and use the Sentry
SaaS free tier (50k events/month) with just the Python SDK.  This is the
pragmatic choice for a solo operator.  The compose slot stays reserved for
when event volume demands self-hosting.

**Recommendation:** Start with SaaS Sentry + SDK only.  Revisit self-hosting
when event volume exceeds free tier.

### 1d. Add root orchestration

**File:** `docker/compose.yml` (create)

A thin root compose that includes all stacks via `include:` (Compose v2.20+):

```yaml
include:
  - proxy/compose.yml
  - authentik/compose.yml
  - temporal/compose.yml
  - opa/compose.yml
  - telemetry/compose.yml
```

Or a `Makefile` / shell script that brings stacks up in dependency order:
proxy first, then the rest.

### Files created/modified

| File | Action |
|---|---|
| `docker/README.md` | Create |
| `docker/create-networks.sh` | Create |
| `docker/.env.example` | Create (shared secrets template) |
| `docker/telemetry/compos.yml` | Rename to `compose.yml` |
| `docker/telemetry/otelcol-config.yaml` | Create |
| `docker/opa/policies/main.rego` | Create (stub) |
| `docker/temporal/config/dynamicconfig/development-sql.yaml` | Create |
| `docker/sentry/compose.yml` | Create (or skip — see recommendation) |
| `docker/authentik/compose.yml` | Modify (secrets → .env) |
| `docker/temporal/compose.yml` | Modify (secrets → .env, healthchecks) |
| `docker/proxy/compose.yml` | Modify (domain → .env) |
| `docker/opa/compose.yml` | Modify (pin version, healthcheck) |
| `docker/telemetry/compose.yml` | Modify (healthcheck) |

---

## Phase 2 — OpenTelemetry Instrumentation

**Goal:** Every FastAPI request produces a trace with a unique trace ID that
propagates through all downstream calls.  This is foundational — Temporal,
OPA, and Sentry all consume trace context.

**Depends on:** Phase 1 (OTel collector running).

### 2a. Add Python dependencies

**File:** `pyproject.toml` or `requirements.txt`

```
opentelemetry-api>=1.24
opentelemetry-sdk>=1.24
opentelemetry-instrumentation-fastapi>=0.45b
opentelemetry-instrumentation-httpx>=0.45b
opentelemetry-exporter-otlp-proto-grpc>=1.24
sentry-sdk[fastapi]>=2.0
```

### 2b. Initialize tracing in app.py

**File:** `sciona/api/app.py` (modify)

Add OTel setup in `_lifespan()` or a separate `sciona/api/telemetry.py`:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_telemetry(app: FastAPI) -> None:
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=os.getenv("OTEL_EXPORTER_ENDPOINT", "http://localhost:4317"))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
```

Call `setup_telemetry(app)` inside `create_app()`.

### 2c. Initialize Sentry SDK

**File:** `sciona/api/app.py` (modify)

```python
import sentry_sdk
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    traces_sample_rate=0.1,
    environment=os.getenv("SCIONA_ENV", "development"),
)
```

Sentry's FastAPI integration auto-captures exceptions and attaches to OTel
traces when both are configured.

### 2d. Add trace context to key operations

**File:** `sciona/api/deps.py` (modify)

Add user context to active span:

```python
from opentelemetry import trace

async def require_auth(...) -> UserProfile:
    ...
    span = trace.get_current_span()
    span.set_attribute("user.id", str(profile.user_id))
    span.set_attribute("user.tier", profile.tier)
    return profile
```

**File:** `sciona/api/routers/bounty.py` (modify)

Add bounty context to spans in each endpoint:

```python
span = trace.get_current_span()
span.set_attribute("bounty.id", str(bounty_id))
span.set_attribute("bounty.action", "fund")
```

### Files created/modified

| File | Action |
|---|---|
| `sciona/api/telemetry.py` | Create (OTel + Sentry init) |
| `sciona/api/app.py` | Modify (call setup_telemetry) |
| `sciona/api/deps.py` | Modify (span attributes on auth) |
| `sciona/api/routers/bounty.py` | Modify (span attributes per endpoint) |
| `sciona/api/routers/verification.py` | Modify (span attributes) |
| `pyproject.toml` | Modify (add OTel + Sentry deps) |

---

## Phase 3 — Temporal Workflow Orchestration

**Goal:** Replace the inline bounty state machine with Temporal workflows.
Each bounty becomes a long-running workflow; state transitions become signals.

**Depends on:** Phase 1 (Temporal server running), Phase 2 (trace context
propagates into workflows).

### 3a. Add Temporal Python SDK

**File:** `pyproject.toml`

```
temporalio>=1.5
```

### 3b. Initialize Temporal client

**File:** `sciona/api/app.py` (modify)

In `_lifespan()`, create and store the Temporal client:

```python
from temporalio.client import Client as TemporalClient

temporal_client = await TemporalClient.connect(
    os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
)
app.state.temporal = temporal_client
```

**File:** `sciona/api/deps.py` (modify)

Add dependency:

```python
def get_temporal(request: Request) -> TemporalClient:
    return request.app.state.temporal
```

### 3c. Define bounty workflow

**File:** `sciona/workflows/bounty_workflow.py` (create)

```python
@workflow.defn
class BountyWorkflow:
    """Long-running workflow for a single bounty lifecycle.

    State: draft → open → submitted → verified → settled
    Signals: fund, submit, cancel, verify_complete, settle
    Queries: get_status, get_submissions
    """

    @workflow.run
    async def run(self, bounty_id: str, escrow_amount: Decimal) -> str:
        # Wait for funding signal
        await workflow.wait_condition(lambda: self._funded)
        # Wait for submission or deadline
        # On submission: trigger verification child workflow
        # On verification complete: trigger settlement activity
        # Return final status

    @workflow.signal
    async def fund(self, stripe_payment_id: str): ...

    @workflow.signal
    async def submit(self, submission_id: str, architect_id: str): ...

    @workflow.signal
    async def cancel(self): ...

    @workflow.query
    def get_status(self) -> str: ...
```

### 3d. Define activities

**File:** `sciona/workflows/bounty_activities.py` (create)

```python
@activity.defn
async def record_funding(bounty_id: str, payment_id: str) -> None:
    """Update bounty status in Supabase after Stripe confirms payment."""

@activity.defn
async def launch_verification(submission_id: str, bounty_id: str) -> str:
    """Kick off sandbox verification run. Returns verification_run_id."""

@activity.defn
async def compute_settlement(bounty_id: str) -> PayoutPlan:
    """Run Shapley computation and return payout plan."""

@activity.defn
async def execute_payouts(payout_plan: PayoutPlan) -> list[str]:
    """Create Stripe transfers for each recipient. Returns transfer IDs."""

@activity.defn
async def record_settlement(bounty_id: str, transfer_ids: list[str]) -> None:
    """Write settlement records to database."""
```

### 3e. Create Temporal worker

**File:** `sciona/workflows/worker.py` (create)

```python
async def main():
    client = await TemporalClient.connect(os.getenv("TEMPORAL_ADDRESS"))
    worker = Worker(
        client,
        task_queue="bounty-lifecycle",
        workflows=[BountyWorkflow],
        activities=[
            record_funding,
            launch_verification,
            compute_settlement,
            execute_payouts,
            record_settlement,
        ],
    )
    await worker.run()
```

Run as: `python -m sciona.workflows.worker`

### 3f. Update bounty router to use workflows

**File:** `sciona/api/routers/bounty.py` (modify)

Replace inline state transitions with workflow signals:

```python
# BEFORE (inline):
validate_transition(bounty["status"], "fund")
await supabase.table("bounties").update({"status": "open"}).eq(...)

# AFTER (workflow):
handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
await handle.signal(BountyWorkflow.fund, stripe_payment_id=payment_id)
```

For `create_bounty`: start a new workflow:

```python
await temporal.start_workflow(
    BountyWorkflow.run,
    args=[str(bounty_id), escrow_amount],
    id=f"bounty-{bounty_id}",
    task_queue="bounty-lifecycle",
)
```

For `get_submission_status` in verification router: query the workflow:

```python
handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
status = await handle.query(BountyWorkflow.get_status)
```

### 3g. Add Temporal worker to docker

**File:** `docker/temporal/compose.yml` (modify)

Add a `worker` service that runs the Python worker alongside the Temporal
server.  Or run it separately as part of the application deployment.

### Files created/modified

| File | Action |
|---|---|
| `sciona/workflows/__init__.py` | Create |
| `sciona/workflows/bounty_workflow.py` | Create |
| `sciona/workflows/bounty_activities.py` | Create |
| `sciona/workflows/worker.py` | Create |
| `sciona/api/app.py` | Modify (Temporal client init) |
| `sciona/api/deps.py` | Modify (get_temporal dependency) |
| `sciona/api/routers/bounty.py` | Modify (workflow signals replace inline state) |
| `sciona/api/routers/verification.py` | Modify (query workflow status) |
| `pyproject.toml` | Modify (add temporalio) |
| `tests/test_bounty_workflow.py` | Create |

---

## Phase 4 — OPA Policy Enforcement

**Goal:** Extract authorization and business rules from application code into
declarative Rego policies evaluated by OPA.

**Depends on:** Phase 1 (OPA running), Phase 2 (trace context for audit).

### 4a. Define policy structure

**File:** `docker/opa/policies/` (create tree)

```
policies/
├── bounty.rego          # Bounty lifecycle rules
├── submission.rego      # Submission authorization
├── payout.rego          # Payout constraint validation
└── data.json            # Static data (tier limits, fee schedules)
```

### 4b. Bounty policies

**File:** `docker/opa/policies/bounty.rego`

```rego
package bounty

default allow_create = false
default allow_fund = false
default allow_cancel = false
default allow_submit = false

# Only non-blacklisted users can create bounties
allow_create {
    not input.user.is_blacklisted
    input.user.tier == "payee"
}

# Only the bounty creator can fund
allow_fund {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status == "draft"
}

# Only non-creators can submit (no self-dealing)
allow_submit {
    input.user.user_id != input.bounty.principal_id
    not input.user.is_blacklisted
    input.bounty.status == "open"
}

# Cancellation: only creator, and only before settlement
allow_cancel {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status in {"draft", "open", "submitted"}
}
```

### 4c. Payout constraint policies

**File:** `docker/opa/policies/payout.rego`

```rego
package payout

# Verify payout plan conserves escrow
valid_plan {
    total := sum([p.amount | p := input.plan.payouts[_]])
    total == input.plan.escrow_amount
}

# Platform fee must be exactly 5%
valid_platform_fee {
    input.plan.platform_amount == input.plan.escrow_amount * 0.05
}
```

### 4d. Create OPA client

**File:** `sciona/api/policy.py` (create)

```python
import httpx
from opentelemetry import trace

_OPA_URL = os.getenv("OPA_URL", "http://localhost:8181")
tracer = trace.get_tracer(__name__)

async def evaluate_policy(
    package: str,
    rule: str,
    input_data: dict,
) -> bool:
    with tracer.start_as_current_span(f"opa.{package}.{rule}"):
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_OPA_URL}/v1/data/{package}/{rule}",
                json={"input": input_data},
            )
            resp.raise_for_status()
            return resp.json().get("result", False)
```

### 4e. Integrate into bounty router

**File:** `sciona/api/routers/bounty.py` (modify)

Before each state transition, evaluate the OPA policy:

```python
from sciona.api.policy import evaluate_policy

@router.post("/{bounty_id}/fund")
async def fund_bounty(...):
    allowed = await evaluate_policy("bounty", "allow_fund", {
        "user": user.model_dump(),
        "bounty": bounty,
    })
    if not allowed:
        raise HTTPException(403, "Policy denied: cannot fund this bounty")
    ...
```

### 4f. Integrate into settlement

**File:** `sciona/workflows/bounty_activities.py` (modify)

After computing payout plan, validate with OPA before executing transfers:

```python
valid = await evaluate_policy("payout", "valid_plan", {
    "plan": plan.model_dump(),
})
if not valid:
    raise ApplicationError("Payout plan failed policy validation")
```

### Files created/modified

| File | Action |
|---|---|
| `docker/opa/policies/bounty.rego` | Create |
| `docker/opa/policies/submission.rego` | Create |
| `docker/opa/policies/payout.rego` | Create |
| `docker/opa/policies/data.json` | Create |
| `sciona/api/policy.py` | Create (OPA client) |
| `sciona/api/routers/bounty.py` | Modify (policy checks before transitions) |
| `sciona/workflows/bounty_activities.py` | Modify (validate payout plan) |
| `tests/test_opa_policies.py` | Create |

---

## Phase 5 — Authentik Enterprise Auth

**Goal:** Add SAML/SSO federation for enterprise clients while preserving
existing Supabase GitHub OAuth for individual users.

**Depends on:** Phase 1 (Authentik running behind proxy).

### 5a. Configure Authentik as SAML/OIDC provider

This is primarily Authentik admin UI configuration, not code:

1. Create an OAuth2/OIDC provider in Authentik pointing to the platform
2. Configure Supabase to accept Authentik as an external OIDC provider
3. Map Authentik groups to platform identity tiers

### 5b. Add SSO login flow to auth router

**File:** `sciona/api/routers/auth.py` (modify)

Add an enterprise login endpoint that redirects to Authentik:

```python
@router.get("/auth/enterprise/login")
async def enterprise_login(org_slug: str):
    """Redirect to Authentik SAML/OIDC flow for the given organization."""
    authentik_url = os.getenv("AUTHENTIK_URL", "https://auth.yourdomain.com")
    return RedirectResponse(
        f"{authentik_url}/application/o/{org_slug}/"
    )

@router.get("/auth/enterprise/callback")
async def enterprise_callback(code: str, state: str):
    """Handle Authentik OIDC callback, create/update user, return JWT."""
    ...
```

### 5c. Add SSO button to frontend

**File:** `frontend/src/pages/Login.tsx` (create or modify Home.tsx)

Add an "Enterprise SSO" login button alongside the existing GitHub OAuth
button.  The button should prompt for org slug then redirect to
`/auth/enterprise/login?org_slug=...`.

### 5d. SCIM provisioning

**File:** `sciona/api/routers/scim.py` (create)

Implement SCIM 2.0 endpoints for Authentik to push user provisioning:

```python
@router.post("/scim/v2/Users")
async def create_scim_user(...): ...

@router.patch("/scim/v2/Users/{id}")
async def update_scim_user(...): ...

@router.delete("/scim/v2/Users/{id}")
async def deactivate_scim_user(...): ...
```

This allows enterprise clients to auto-provision/deprovision platform
accounts when employees join/leave their organization.

### Files created/modified

| File | Action |
|---|---|
| `sciona/api/routers/auth.py` | Modify (enterprise login/callback) |
| `sciona/api/routers/scim.py` | Create (SCIM provisioning) |
| `sciona/api/app.py` | Modify (mount SCIM router) |
| `frontend/src/pages/Login.tsx` | Create (SSO button) |
| `frontend/src/components/Layout.tsx` | Modify (login link) |

---

## Phase 6 — Frontend Integration & Polish

**Goal:** Wire frontend to real API (remove mock fallback), add auth state
management, and surface infrastructure status.

**Depends on:** Phases 2-5 (backend integrations complete).

### 6a. Auth state management

**File:** `frontend/src/auth/AuthContext.tsx` (create)

React context providing:
- Current user profile (from `/auth/me`)
- JWT token stored in localStorage
- Login/logout functions
- Loading state

### 6b. Protected routes

**File:** `frontend/src/App.tsx` (modify)

Wrap bounty creation, submission, and profile routes in auth guard:

```typescript
<Route path="bounties/new" element={<RequireAuth><CreateBounty /></RequireAuth>} />
```

### 6c. Real-time bounty status

**File:** `frontend/src/pages/BountyDetail.tsx` (modify)

Poll `/submissions/{id}/status` (which now queries Temporal workflow state)
to show live verification progress.  Consider Supabase Realtime subscription
for push updates.

### 6d. Remove mock fallback

**File:** `frontend/src/api/client.ts` (modify)

Remove `USE_MOCK` conditional.  Keep `mock.ts` for Storybook/tests only.

### Files created/modified

| File | Action |
|---|---|
| `frontend/src/auth/AuthContext.tsx` | Create |
| `frontend/src/auth/RequireAuth.tsx` | Create |
| `frontend/src/App.tsx` | Modify (auth provider, protected routes) |
| `frontend/src/pages/BountyDetail.tsx` | Modify (live status polling) |
| `frontend/src/api/client.ts` | Modify (remove mock conditional) |

---

## Dependency Graph

```
Phase 1 (fix docker scaffolding)
  │
  ├──→ Phase 2 (OpenTelemetry + Sentry)
  │       │
  │       ├──→ Phase 3 (Temporal workflows)
  │       │       │
  │       │       └──→ ╮
  │       │            │
  │       └──→ Phase 4 (OPA policies)
  │                    │
  ├──→ Phase 5 (Authentik SSO) ←──independent──→ │
  │                                               │
  └────────────────────────────────────────────→ Phase 6 (frontend integration)
```

## Parallelization

```
Sequential (single agent):   1 → 2 → 3 → 4 → 5 → 6

Two agents:                  Agent A: 1 → 2 → 3 → 6
                             Agent B: 1 → 5 → 4

Three agents:                Agent A: 1 → 2 → 3
                             Agent B: 1 → 5
                             Agent C: 1 → 4 → 6
```

Phase 1 is the shared prerequisite.  After that:
- **Phases 2→3** is the critical path (OTel must exist before Temporal, Temporal
  must exist before workflow-based frontend)
- **Phase 4** (OPA) is independent of Temporal — only needs OTel for tracing
- **Phase 5** (Authentik) is fully independent of 2/3/4
- **Phase 6** gates on all backend phases

---

## File Summary

| Phase | New files | Modified files |
|---|---|---|
| 1. Docker scaffolding | ~10 (configs, scripts, docs) | 5 compose files |
| 2. OpenTelemetry + Sentry | 1 (`telemetry.py`) | 4 (app, deps, routers, pyproject) |
| 3. Temporal workflows | 5 (`workflows/` package) | 4 (app, deps, bounty router, verification router) |
| 4. OPA policies | 5 (policies + client) | 2 (bounty router, activities) |
| 5. Authentik SSO | 2 (SCIM router, login page) | 3 (auth router, app, layout) |
| 6. Frontend integration | 2 (auth context, guard) | 3 (App, BountyDetail, client) |
| **Total** | **~25** | **~15** |

---

## Priority Recommendation

The gap analysis asks: Temporal or Firecracker first?

**Temporal first.** Reasoning:
1. The bounty lifecycle is the revenue path — every dollar flows through it
2. The current inline state machine in `bounty_state.py` has no retry, no
   audit trail, no crash recovery — a failed payout is silent data loss
3. Temporal gives you the execution history that Stripe disputes require
4. Firecracker sandbox is important but is on the verification path, not
   the money path — and verification currently works (just not isolated)
5. OTel (Phase 2) should come before Temporal because you want traces
   from day one of workflow execution

**Recommended execution order:** 1 → 2 → 3 → 5 → 4 → 6
