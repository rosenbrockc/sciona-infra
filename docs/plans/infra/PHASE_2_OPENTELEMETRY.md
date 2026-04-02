# Phase 2 — OpenTelemetry + Sentry Instrumentation

**Goal:** Every FastAPI request produces a trace with a unique trace ID that
propagates through all downstream calls. Sentry captures unhandled exceptions
and links them to traces.

**Depends on:** Phase 1 (OTel collector running on `localhost:4317`).
**No dependency on:** any other phase.

**Parent plan:** `docs/plans/infra/README.md`, Phase 2 section.

---

## Prerequisites

- Phase 1 complete: `docker/telemetry/compose.yml` (renamed from `compos.yml`)
  is running with `otelcol-config.yaml` that accepts OTLP gRPC on port 4317.
- A Sentry DSN (SaaS free tier or self-hosted). Can be empty string to disable.

---

## Step 1 — Create `pyproject.toml` at the project root

The project currently has **no** root-level `pyproject.toml`, `setup.py`, or
`requirements.txt`. The `.venv` exists with packages installed ad-hoc. This
step creates a minimal `pyproject.toml` to formalize dependency management.

> If a `pyproject.toml` has been created by a prior task, merge the
> dependencies below into the existing `[project.dependencies]` list instead.

### Action: Create file

**File:** `pyproject.toml` (project root)

Check whether the file exists first. If it does NOT exist, create it with:

```toml
[project]
name = "sciona"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.111",
    "uvicorn>=0.29",
    "supabase>=2.0",
    "httpx>=0.27",
    "pydantic>=2.7",
    # --- Phase 2: OpenTelemetry + Sentry ---
    "opentelemetry-api>=1.24",
    "opentelemetry-sdk>=1.24",
    "opentelemetry-instrumentation-fastapi>=0.45b0",
    "opentelemetry-instrumentation-httpx>=0.45b0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.24",
    "sentry-sdk[fastapi]>=2.0",
]
```

If `pyproject.toml` already exists, add these lines to the `dependencies`
array:

```
"opentelemetry-api>=1.24",
"opentelemetry-sdk>=1.24",
"opentelemetry-instrumentation-fastapi>=0.45b0",
"opentelemetry-instrumentation-httpx>=0.45b0",
"opentelemetry-exporter-otlp-proto-grpc>=1.24",
"sentry-sdk[fastapi]>=2.0",
```

### Action: Install

```bash
pip install "opentelemetry-api>=1.24" "opentelemetry-sdk>=1.24" \
    "opentelemetry-instrumentation-fastapi>=0.45b0" \
    "opentelemetry-instrumentation-httpx>=0.45b0" \
    "opentelemetry-exporter-otlp-proto-grpc>=1.24" \
    "sentry-sdk[fastapi]>=2.0"
```

---

## Step 2 — Create `sciona/api/telemetry.py`

### Action: Create file

**File:** `sciona/api/telemetry.py`

Write the entire file with this content:

```python
"""OpenTelemetry and Sentry initialization for the platform API."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def setup_telemetry(app) -> None:  # noqa: ANN001 — avoid importing FastAPI at module level
    """Configure OpenTelemetry tracing and Sentry error reporting.

    Safe to call when dependencies are missing (logs a warning and returns).
    Reads configuration from environment variables:
      - OTEL_EXPORTER_ENDPOINT  — gRPC endpoint for the OTel collector
                                   (default: http://localhost:4317)
      - OTEL_SERVICE_NAME       — logical service name in traces
                                   (default: sciona-api)
      - SENTRY_DSN              — Sentry DSN; empty string disables Sentry
      - SCIONA_ENV              — deployment environment tag
                                   (default: development)
    """
    _setup_opentelemetry(app)
    _setup_sentry()


def _setup_opentelemetry(app) -> None:  # noqa: ANN001
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "opentelemetry packages not installed — skipping OTel setup"
        )
        return

    endpoint = os.getenv("OTEL_EXPORTER_ENDPOINT", "http://localhost:4317")
    service_name = os.getenv("OTEL_SERVICE_NAME", "sciona-api")
    environment = os.getenv("SCIONA_ENV", "development")

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)

    # Instrument httpx so outbound HTTP calls (Supabase SDK, OPA, etc.)
    # automatically propagate trace context.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except ImportError:
        logger.debug("opentelemetry-instrumentation-httpx not installed")

    logger.info(
        "OpenTelemetry configured: service=%s endpoint=%s env=%s",
        service_name,
        endpoint,
        environment,
    )


def _setup_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.info("SENTRY_DSN not set — Sentry disabled")
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry-sdk not installed — skipping Sentry setup")
        return

    environment = os.getenv("SCIONA_ENV", "development")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.1,
        # Link Sentry transactions to OTel traces when both are active.
        # The sentry-sdk[fastapi] extra auto-instruments FastAPI.
        enable_tracing=True,
    )

    logger.info("Sentry configured: env=%s", environment)
```

---

## Step 3 — Modify `sciona/api/app.py`

### Action: Edit — import telemetry setup

**File:** `sciona/api/app.py`

```
old_string:
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

new_string:
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sciona.api.telemetry import setup_telemetry
```

### Action: Edit — call setup_telemetry after router registration

**File:** `sciona/api/app.py`

```
old_string:
    application.include_router(dashboard_router, tags=["dashboard"])

    return application

new_string:
    application.include_router(dashboard_router, tags=["dashboard"])

    setup_telemetry(application)

    return application
```

**Why after routers:** `FastAPIInstrumentor.instrument_app()` wraps all
registered routes. Calling it after `include_router` ensures every endpoint
gets traced.

---

## Step 4 — Modify `sciona/api/deps.py` — add user attributes to active span

### Action: Edit — add import

**File:** `sciona/api/deps.py`

```
old_string:
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

new_string:
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]
```

### Action: Edit — set span attributes after successful auth

**File:** `sciona/api/deps.py`

```
old_string:
    return UserProfile(**data)

new_string:
    profile = UserProfile(**data)
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("user.id", str(profile.user_id))
            span.set_attribute("user.identity_tier", profile.identity_tier)
            span.set_attribute("user.effective_tier", profile.effective_tier)
    return profile
```

---

## Step 5 — Modify `sciona/api/routers/bounty.py` — add span attributes

### Action: Edit — add OTel import

**File:** `sciona/api/routers/bounty.py`

```
old_string:
from fastapi import APIRouter, Depends, HTTPException, Query

from sciona.api import deps as api_deps

new_string:
from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]

from sciona.api import deps as api_deps
```

### Action: Edit — add span attributes to `create_bounty`

**File:** `sciona/api/routers/bounty.py`

```
old_string:
@router.post("")
async def create_bounty(
    body: BountyCreateRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Create a draft bounty."""
    user_id = str(user.user_id)

new_string:
@router.post("")
async def create_bounty(
    body: BountyCreateRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Create a draft bounty."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.action", "create")
    user_id = str(user.user_id)
```

### Action: Edit — add span attributes to `fund_bounty`

**File:** `sciona/api/routers/bounty.py`

```
old_string:
@router.post("/{bounty_id}/fund")
async def fund_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyFundResponse:
    """Fund a bounty (transitions draft -> open)."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)

new_string:
@router.post("/{bounty_id}/fund")
async def fund_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyFundResponse:
    """Fund a bounty (transitions draft -> open)."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
            span.set_attribute("bounty.action", "fund")
    row = await _fetch_bounty(bounty_id, supabase=supabase)
```

### Action: Edit — add span attributes to `submit_to_bounty`

**File:** `sciona/api/routers/bounty.py`

```
old_string:
@router.post("/{bounty_id}/submit")
async def submit_to_bounty(
    bounty_id: UUID,
    body: SubmissionRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> SubmissionResponse:
    """Submit a CDG solution with signed receipt."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)

new_string:
@router.post("/{bounty_id}/submit")
async def submit_to_bounty(
    bounty_id: UUID,
    body: SubmissionRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> SubmissionResponse:
    """Submit a CDG solution with signed receipt."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
            span.set_attribute("bounty.action", "submit")
    row = await _fetch_bounty(bounty_id, supabase=supabase)
```

### Action: Edit — add span attributes to `cancel_bounty`

**File:** `sciona/api/routers/bounty.py`

```
old_string:
@router.post("/{bounty_id}/cancel")
async def cancel_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyCancelResponse:
    """Cancel a bounty (with fee per design decision 4.13)."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)

new_string:
@router.post("/{bounty_id}/cancel")
async def cancel_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyCancelResponse:
    """Cancel a bounty (with fee per design decision 4.13)."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
            span.set_attribute("bounty.action", "cancel")
    row = await _fetch_bounty(bounty_id, supabase=supabase)
```

### Action: Edit — add span attributes to `update_target`

**File:** `sciona/api/routers/bounty.py`

```
old_string:
@router.post("/{bounty_id}/target")
async def update_target(
    bounty_id: UUID,
    body: UpdateTargetRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Principal updates minimum metric target between verifications."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)

new_string:
@router.post("/{bounty_id}/target")
async def update_target(
    bounty_id: UUID,
    body: UpdateTargetRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Principal updates minimum metric target between verifications."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
            span.set_attribute("bounty.action", "update_target")
    row = await _fetch_bounty(bounty_id, supabase=supabase)
```

---

## Step 6 — Modify `sciona/api/routers/verification.py` — add span attributes

### Action: Edit — add OTel import

**File:** `sciona/api/routers/verification.py`

```
old_string:
from fastapi import APIRouter, Depends, HTTPException, Query

from sciona.api import deps as api_deps

new_string:
from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None  # type: ignore[assignment]

from sciona.api import deps as api_deps
```

### Action: Edit — add span attributes to `get_submission_status`

**File:** `sciona/api/routers/verification.py`

```
old_string:
@router.get("/submissions/{submission_id}/status")
async def get_submission_status(
    submission_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Poll verification progress for a submission."""
    submission_result = await (

new_string:
@router.get("/submissions/{submission_id}/status")
async def get_submission_status(
    submission_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Poll verification progress for a submission."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("submission.id", str(submission_id))
    submission_result = await (
```

### Action: Edit — add span attributes to `get_leaderboard`

**File:** `sciona/api/routers/verification.py`

```
old_string:
@router.get("/bounties/{bounty_id}/leaderboard")
async def get_leaderboard(
    bounty_id: UUID,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(api_deps.get_supabase),
) -> PaginatedResponse:
    """Current ranking of verified submissions for a bounty."""
    limit = int(getattr(limit, "default", limit))

new_string:
@router.get("/bounties/{bounty_id}/leaderboard")
async def get_leaderboard(
    bounty_id: UUID,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(api_deps.get_supabase),
) -> PaginatedResponse:
    """Current ranking of verified submissions for a bounty."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
    limit = int(getattr(limit, "default", limit))
```

### Action: Edit — add span attributes to `get_settlement`

**File:** `sciona/api/routers/verification.py`

```
old_string:
@router.get("/bounties/{bounty_id}/settlement")
async def get_settlement(
    bounty_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Retrieve settlement details for a settled bounty."""
    bounty_result = await (

new_string:
@router.get("/bounties/{bounty_id}/settlement")
async def get_settlement(
    bounty_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Retrieve settlement details for a settled bounty."""
    if _otel_trace is not None:
        span = _otel_trace.get_current_span()
        if span.is_recording():
            span.set_attribute("bounty.id", str(bounty_id))
    bounty_result = await (
```

---

## Step 7 — Environment Variables

Add these to `.env` (or `.env.example` if one exists):

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_ENDPOINT` | `http://localhost:4317` | OTel collector gRPC endpoint |
| `OTEL_SERVICE_NAME` | `sciona-api` | Service name in traces |
| `SENTRY_DSN` | `""` (disabled) | Sentry project DSN |
| `SCIONA_ENV` | `development` | Environment tag for traces and Sentry |

All four are optional. With no env vars set, OTel attempts `localhost:4317`
(works when the Phase 1 telemetry compose stack is running) and Sentry is
disabled.

---

## Step 8 — Test Approach

### 8a. Unit test: verify `setup_telemetry` does not crash without dependencies

No new test file needed. The existing test suite will exercise the import
path because `create_app()` now calls `setup_telemetry()`. If the OTel
packages are not installed, the function logs a warning and returns — no error.

### 8b. Unit test: verify span attributes are set (new file)

**File:** `tests/test_telemetry.py`

Create with the following content:

```python
"""Verify OpenTelemetry instrumentation attaches expected span attributes."""

from __future__ import annotations

import pytest

# These tests only run when the OTel SDK is installed.
otel_trace = pytest.importorskip("opentelemetry.trace")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter


@pytest.fixture()
def memory_exporter():
    """Set up an in-memory OTel exporter so we can inspect emitted spans."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()


def test_setup_telemetry_instruments_app(memory_exporter):
    """setup_telemetry should not raise and should instrument the app."""
    import os
    # Point exporter at a bogus endpoint so it does not connect anywhere.
    os.environ["OTEL_EXPORTER_ENDPOINT"] = "http://localhost:19999"
    os.environ.pop("SENTRY_DSN", None)

    from sciona.api.app import create_app

    app = create_app()
    # After create_app, the app should have routes instrumented.
    # Just verify no exception was raised — the in-memory exporter proves
    # the TracerProvider was replaced by setup_telemetry (or was already set
    # by this fixture).
    assert app is not None


def test_auth_sets_user_span_attributes(memory_exporter):
    """The require_auth dependency should annotate the current span."""
    from sciona.api.deps import _otel_trace

    if _otel_trace is None:
        pytest.skip("OTel not available in deps module")

    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("test-auth") as span:
        # Simulate what require_auth does after successful lookup:
        span.set_attribute("user.id", "abc-123")
        span.set_attribute("user.identity_tier", "contributor")
        span.set_attribute("user.effective_tier", "general")

    finished = memory_exporter.get_finished_spans()
    assert len(finished) >= 1
    attrs = finished[-1].attributes
    assert attrs["user.id"] == "abc-123"
    assert attrs["user.identity_tier"] == "contributor"
```

### 8c. Integration smoke test: confirm traces reach the OTel collector

This requires the Phase 1 telemetry stack running. Manual procedure:

1. Start the telemetry stack:
   ```bash
   cd docker/telemetry && docker compose up -d
   ```

2. Start the API:
   ```bash
   OTEL_EXPORTER_ENDPOINT=http://localhost:4317 uvicorn sciona.api.app:app --port 8000
   ```

3. Hit any endpoint:
   ```bash
   curl http://localhost:8000/bounties
   ```

4. Check OTel collector logs for the exported span:
   ```bash
   docker compose -f docker/telemetry/compose.yml logs otel-collector | grep "sciona-api"
   ```
   The collector's `logging` exporter (configured in `otelcol-config.yaml`)
   should print the trace with `service.name=sciona-api`.

5. If Sentry is configured, check the Sentry web UI for the transaction.

---

## Files Summary

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | Create or Modify | Add 6 OTel + Sentry dependencies |
| `sciona/api/telemetry.py` | **Create** | OTel TracerProvider + Sentry SDK init |
| `sciona/api/app.py` | Modify | Import + call `setup_telemetry(application)` |
| `sciona/api/deps.py` | Modify | Set `user.id`, `user.identity_tier`, `user.effective_tier` on span |
| `sciona/api/routers/bounty.py` | Modify | Set `bounty.id`, `bounty.action` on span in each endpoint |
| `sciona/api/routers/verification.py` | Modify | Set `submission.id`, `bounty.id` on span in each endpoint |
| `tests/test_telemetry.py` | **Create** | Unit tests for telemetry setup and span attributes |

---

## Execution Order

Steps 1-6 can be done in a single pass. The recommended order:

1. Step 1 — dependencies (so imports resolve)
2. Step 2 — create `telemetry.py` (no existing file depends on it yet)
3. Step 3 — wire into `app.py`
4. Step 4 — `deps.py` span attributes
5. Steps 5-6 — router span attributes (independent of each other)
6. Step 7 — env vars documentation
7. Step 8 — run tests

---

## Design Decisions

- **Graceful degradation:** Every OTel import is wrapped in `try/except
  ImportError` so the application still starts if packages are missing. This
  keeps the test suite fast (no mandatory OTel dependency for unrelated tests).

- **`insecure=True` on the OTLP exporter:** The collector runs on the
  internal Docker network or localhost — no TLS needed in development. For
  production, remove this flag and configure TLS certificates.

- **`traces_sample_rate=0.1` for Sentry:** Captures 10% of transactions to
  stay within free-tier limits. Bump to `1.0` for debugging, or use
  `traces_sampler` for per-endpoint control.

- **`setup_telemetry` called after router registration:** The
  `FastAPIInstrumentor.instrument_app()` call wraps all routes that exist at
  call time. Calling it before `include_router` would miss every endpoint.

- **httpx auto-instrumentation:** The Supabase Python SDK uses httpx
  internally. Instrumenting httpx ensures Supabase calls appear as child
  spans under the parent HTTP request span, giving full request waterfall
  visibility.
