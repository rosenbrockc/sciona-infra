# Phase B: Platform API -- Implementation Plan

### 1. PostgreSQL Schema

The following tables go into Supabase PostgreSQL. They are designed to be the source of truth for all relational data, with Memgraph reserved for graph-shaped provenance queries only (per TECH_GAP 3.4).

```sql
-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =================================================================
-- USERS & AUTH
-- =================================================================
CREATE TABLE users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id     BIGINT UNIQUE NOT NULL,
    github_login  TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    avatar_url    TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    identity_tier TEXT NOT NULL DEFAULT 'contributor'
                  CHECK (identity_tier IN ('contributor', 'payee')),
    stripe_account_id TEXT,                -- NULL until KYC
    reputation_score  INTEGER NOT NULL DEFAULT 0,
    is_blacklisted    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_github_login ON users (github_login);

-- =================================================================
-- ATOMS & VERSIONS (content-addressed + semver labels, per 4.9)
-- =================================================================
CREATE TABLE atoms (
    atom_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fqdn          TEXT UNIQUE NOT NULL,       -- e.g. "pkg.mod.bandpass_filter"
    owner_id      UUID NOT NULL REFERENCES users(user_id),
    domain_tags   TEXT[] NOT NULL DEFAULT '{}',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'superseded', 'flagged', 'withdrawn')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE atom_versions (
    version_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    content_hash  TEXT UNIQUE NOT NULL,       -- full SHA-256 (64 hex chars)
    semver        TEXT NOT NULL,              -- e.g. "1.2.3"
    is_latest     BOOLEAN NOT NULL DEFAULT FALSE,
    derives_from  UUID REFERENCES atom_versions(version_id),  -- lineage
    s3_key        TEXT NOT NULL,              -- atoms/{content_hash}.tar.gz
    fingerprint   TEXT NOT NULL,              -- AST fingerprint (full SHA-256)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, semver)
);

CREATE INDEX idx_atom_versions_atom ON atom_versions (atom_id);
CREATE INDEX idx_atom_versions_hash ON atom_versions (content_hash);

-- Authorship (multi-author, per 4.3)
CREATE TABLE atom_authors (
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    user_id       UUID NOT NULL REFERENCES users(user_id),
    contribution_share NUMERIC(5,4) NOT NULL DEFAULT 1.0
                  CHECK (contribution_share > 0 AND contribution_share <= 1),
    PRIMARY KEY (atom_id, user_id)
);

-- Hyperparams (mirrors existing manifest.sqlite schema)
CREATE TABLE hyperparams (
    hp_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('int', 'float', 'categorical', 'bool')),
    default_value JSONB,
    min_value     JSONB,
    max_value     JSONB,
    step_value    JSONB,
    log_scale     BOOLEAN NOT NULL DEFAULT FALSE,
    choices_json  JSONB,
    constraints_json JSONB,
    semantic_role TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'blocked', 'deprecated')),
    UNIQUE (atom_id, name)
);

-- Benchmarks (per 4.12)
CREATE TABLE atom_benchmarks (
    benchmark_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id    UUID NOT NULL REFERENCES atom_versions(version_id),
    benchmark_name TEXT NOT NULL,
    metric_name   TEXT NOT NULL,
    metric_value  DOUBLE PRECISION NOT NULL,
    dataset_tag   TEXT NOT NULL DEFAULT '',
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =================================================================
-- BOUNTIES (state machine per 2.5, cancellation per 4.13)
-- =================================================================
CREATE TABLE bounties (
    bounty_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id  UUID NOT NULL REFERENCES users(user_id),
    title         TEXT NOT NULL,
    escrow_amount NUMERIC(12,2) NOT NULL CHECK (escrow_amount > 0),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'open', 'submitted', 'verified',
                                    'settled', 'expired', 'cancelled')),
    deadline      TIMESTAMPTZ,              -- submission window end
    tier          TEXT NOT NULL DEFAULT 'standard'
                  CHECK (tier IN ('standard', 'heavy', 'gpu')),
    verification_budget INTEGER NOT NULL DEFAULT 5,
    verifications_used  INTEGER NOT NULL DEFAULT 0,
    config_yml    JSONB NOT NULL DEFAULT '{}',
    flare_payload JSONB,                    -- frozen FlarePayload
    sciona_yml_s3  TEXT,                     -- S3 key for sciona.yml
    dataset_s3    TEXT,                     -- S3 prefix for data
    public_split_hash TEXT,
    blind_split_hash  TEXT,
    cancellation_fee NUMERIC(12,2) DEFAULT 0,
    reposted_from UUID REFERENCES bounties(bounty_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bounties_status ON bounties (status);
CREATE INDEX idx_bounties_principal ON bounties (principal_id);

-- =================================================================
-- SUBMISSIONS
-- =================================================================
CREATE TABLE submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES bounties(bounty_id),
    architect_id  UUID NOT NULL REFERENCES users(user_id),
    cdg_hash      TEXT NOT NULL,            -- content-addressed CDG fingerprint
    atom_versions JSONB NOT NULL,           -- {fqdn: content_hash}
    receipt_s3    TEXT NOT NULL,            -- S3 key for signed receipt
    receipt_json  JSONB NOT NULL,
    claimed_metric_name  TEXT NOT NULL,
    claimed_metric_value DOUBLE PRECISION NOT NULL,
    verified_metric_value DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'pending'
                  CHECK (verification_status IN ('pending', 'receipt_valid',
                         'public_verified', 'blind_verified', 'rejected')),
    is_winner     BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at   TIMESTAMPTZ
);

CREATE INDEX idx_submissions_bounty ON submissions (bounty_id);

-- =================================================================
-- PAYOUTS (exact arithmetic, per System Invariant 1)
-- =================================================================
CREATE TABLE payouts (
    payout_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES bounties(bounty_id),
    user_id       UUID NOT NULL REFERENCES users(user_id),
    role          TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount        NUMERIC(12,2) NOT NULL,
    shapley_value TEXT,                     -- exact rational, e.g. "3/7"
    stripe_transfer_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'kyc_hold', 'transferred', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Key design notes:
- `NUMERIC(12,2)` for all financial fields (exact decimal arithmetic, per payout conservation invariant).
- `atom_versions.content_hash` is full SHA-256 (64 hex chars), per TECH_GAP P0 fix.
- `bounties.config_yml` stores the full `config.yml` as JSONB, including Principal-configured params from 5.16.
- `submissions.receipt_json` includes `bounty_id` for replay prevention per 5.17.

### 2. FastAPI Router Structure

New package: `sciona/api/`

```
sciona/api/
    __init__.py
    app.py              # FastAPI app + lifespan
    deps.py             # Dependency injection
    models.py           # Request/response Pydantic models (API-layer, not domain)
    routers/
        __init__.py
        auth.py         # GitHub OAuth + JWT
        registry.py     # Atom CRUD
        bounty.py       # Bounty lifecycle
        catalog.py      # Catalog search + manifest download
```

**Endpoint signatures:**

```python
# --- auth.py ---
@router.get("/auth/github/authorize")
async def github_authorize() -> RedirectResponse:
    """Redirect to GitHub OAuth authorize URL (device flow for CLI)."""

@router.post("/auth/github/callback")
async def github_callback(code: str, state: str) -> TokenResponse:
    """Exchange GitHub auth code for platform JWT."""

@router.get("/auth/github/device")
async def github_device_start() -> DeviceFlowResponse:
    """Start GitHub device flow (returns device_code, user_code, verification_uri)."""

@router.post("/auth/github/device/poll")
async def github_device_poll(device_code: str) -> TokenResponse | PendingResponse:
    """Poll for device flow completion."""

@router.get("/auth/me")
async def get_me(user: User = Depends(require_auth)) -> UserResponse:
    """Return current authenticated user."""

# --- registry.py ---
@router.post("/atoms")
async def publish_atom(
    body: AtomPublishRequest,
    user: User = Depends(require_auth),
) -> AtomPublishResponse:
    """Publish a new atom or new version of existing atom."""

@router.get("/atoms/{fqdn}")
async def get_atom(fqdn: str) -> AtomDetailResponse:
    """Get atom metadata + latest version."""

@router.get("/atoms/{fqdn}/versions")
async def list_versions(fqdn: str) -> list[AtomVersionResponse]:
    """List all versions of an atom."""

@router.get("/atoms/{fqdn}/versions/{semver}")
async def get_version(fqdn: str, semver: str) -> AtomVersionResponse:
    """Get a specific version by semver."""

@router.get("/atoms")
async def search_atoms(
    q: str = "",
    domain_tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedResponse[AtomSummaryResponse]:
    """Search/list atoms with optional filters."""

# --- bounty.py ---
@router.post("/bounties")
async def create_bounty(
    body: BountyCreateRequest,
    user: User = Depends(require_auth),
) -> BountyResponse:
    """Create a draft bounty."""

@router.post("/bounties/{bounty_id}/fund")
async def fund_bounty(
    bounty_id: UUID,
    user: User = Depends(require_auth),
) -> BountyFundResponse:
    """Fund a bounty (returns Stripe checkout URL). Transitions draft -> open."""

@router.post("/bounties/{bounty_id}/submit")
async def submit_to_bounty(
    bounty_id: UUID,
    body: SubmissionRequest,
    user: User = Depends(require_auth),
) -> SubmissionResponse:
    """Submit a CDG solution with signed receipt."""

@router.post("/bounties/{bounty_id}/cancel")
async def cancel_bounty(
    bounty_id: UUID,
    user: User = Depends(require_auth),
) -> BountyCancelResponse:
    """Cancel a bounty (with fee per 4.13)."""

@router.get("/bounties/{bounty_id}")
async def get_bounty(bounty_id: UUID) -> BountyDetailResponse:
    """Get bounty details including submission count and status."""

@router.get("/bounties")
async def list_bounties(
    status: str | None = None,
    domain_tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedResponse[BountySummaryResponse]:
    """List bounties with optional filters."""

@router.post("/bounties/{bounty_id}/target")
async def update_target(
    bounty_id: UUID,
    body: UpdateTargetRequest,
    user: User = Depends(require_auth),
) -> BountyResponse:
    """Principal updates minimum metric target between verifications (per 4.16)."""

# --- catalog.py ---
@router.get("/catalog/search")
async def catalog_search(
    q: str,
    domain_tag: str | None = None,
    limit: int = 50,
) -> list[CatalogEntry]:
    """Full-text search across catalog."""

@router.get("/catalog/manifest")
async def download_manifest() -> FileResponse:
    """Download latest manifest.sqlite snapshot."""
```

### 3. Auth Middleware Design

The auth system is located in `sciona/api/deps.py` and `sciona/api/routers/auth.py`.

**JWT signing:** Use PyJWT with an RSA key pair. In production, the private key is stored in AWS KMS (via `boto3`); locally, a file-based key at `~/.sciona/jwt_key.pem`.

```python
# deps.py -- dependency injection sketch

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer_scheme = HTTPBearer()

async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """Yield a connection from the asyncpg pool."""
    async with app.state.db_pool.acquire() as conn:
        yield conn

async def get_graph_driver():
    """Return the neo4j async driver (for Memgraph)."""
    return app.state.graph_driver

async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db = Depends(get_db),
) -> User:
    """Decode and validate platform JWT. Returns User row or 401."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired — run `sciona login`")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", payload["sub"])
    if not user:
        raise HTTPException(401, "User not found")
    if user["is_blacklisted"]:
        raise HTTPException(403, "Account suspended")
    return User(**dict(user))
```

**Token fields:**

```json
{
  "sub": "<user_id UUID>",
  "ghid": 12345678,
  "login": "username",
  "tier": "contributor",
  "iat": 1711843200,
  "exp": 1714435200
}
```

30-day expiry. No refresh tokens. `sciona login` re-authenticates from scratch.

**Device flow for CLI:** The CLI cannot open a redirect callback server (it may be SSH). GitHub Device Flow (RFC 8628) is the right choice:

1. CLI calls `POST /auth/github/device` which hits GitHub's device flow endpoint.
2. CLI prints: "Open https://github.com/login/device and enter code: ABCD-1234".
3. CLI polls `POST /auth/github/device/poll` every 5 seconds.
4. When user completes browser auth, the platform exchanges the device code for a GitHub access token, fetches profile, upserts user, issues JWT.
5. CLI stores JWT to `~/.sciona/config` (TOML or JSON, matching existing config pattern).

### 4. Bounty State Machine

Transitions are enforced in a single function to prevent invalid state changes:

```python
VALID_TRANSITIONS = {
    ("draft", "open"):       "fund",
    ("draft", "cancelled"):  "cancel_draft",
    ("open", "submitted"):   "submit",       # first submission received
    ("open", "expired"):     "expire",        # deadline passed, no submissions
    ("open", "cancelled"):   "cancel_open",
    ("submitted", "verified"): "verify",
    ("submitted", "expired"):  "expire",
    ("verified", "settled"):   "settle",
    # "submitted" stays "submitted" on additional submissions
}

def transition_bounty(bounty: Bounty, action: str) -> Bounty:
    key = (bounty.status, TARGET_STATUS[action])
    if key not in VALID_TRANSITIONS:
        raise InvalidTransition(f"Cannot {action} bounty in {bounty.status} state")
    ...
```

**Cancellation fee logic** (per 4.13):

```python
def compute_cancellation_fee(bounty: Bounty, has_submissions: bool) -> Decimal:
    if bounty.status not in ("draft", "open"):
        raise InvalidTransition("Cannot cancel after deadline")
    if has_submissions:
        return bounty.escrow_amount * Decimal("0.25")  # 25%
    return bounty.escrow_amount * Decimal("0.10")       # 10%
```

**Expiry:** A background task (asyncio scheduled task in the Fargate process, or a Lambda on EventBridge schedule) runs hourly, querying `SELECT * FROM bounties WHERE status IN ('open', 'submitted') AND deadline < now()` and transitioning them to `expired`.

### 5. SQLite Snapshot Generation Pipeline

This is the bridge between the platform PostgreSQL and the offline CLI. The existing `load_hyperparams_manifest_sqlite()` in `/Users/conrad/personal/sciona/sciona/architect/hyperparams.py` already reads `manifest.sqlite` with the schema: `atoms(atom_id, fqdn, status)` and `hyperparams(hp_id, atom_id, name, ...)`. The platform must generate this exact schema.

**Pipeline:**

1. On atom publish (after PostgreSQL write succeeds), enqueue an async job.
2. The job queries PostgreSQL:
   ```sql
   SELECT a.atom_id, a.fqdn, a.status, a.domain_tags, a.description
   FROM atoms a WHERE a.status = 'approved';
   
   SELECT h.* FROM hyperparams h
   JOIN atoms a ON h.atom_id = a.atom_id
   WHERE a.status = 'approved' AND h.status = 'approved';
   
   SELECT ab.* FROM atom_benchmarks ab
   JOIN atom_versions av ON ab.version_id = av.version_id
   JOIN atoms a ON av.atom_id = a.atom_id
   WHERE a.status = 'approved' AND av.is_latest = TRUE;
   ```
3. Write results into a fresh in-memory SQLite database using the existing schema (matching what `_create_test_db` in `tests/test_hyperparams.py` creates).
4. Upload to S3 `manifests/manifest.sqlite` (versioned with a timestamp suffix for rollback).
5. Optionally publish an SNS notification so other Fargate tasks refresh.

**New module:** `sciona/api/snapshot.py`

**CLI side:** `sciona catalog sync` downloads `manifests/manifest.sqlite` from S3 to `~/.sciona/manifest.sqlite`. The existing `load_manifest()` in `hyperparams.py` already supports reading from an arbitrary path -- the CLI just needs to point it there.

### 6. CLI Command Signatures

New commands added to `sciona/cli.py` following the existing argparse pattern (no click migration needed -- the entire CLI uses argparse today):

```python
# --- login ---
login_parser = subparsers.add_parser("login", help="Authenticate with the SCIONA platform via GitHub")
login_parser.add_argument("--api-url", type=str, default=None, help="Platform API URL override")

# --- catalog sync ---
# Extend the existing "sources" subparser group, OR create a new "catalog" group:
catalog_parser = subparsers.add_parser("catalog", help="Manage the global atom catalog")
catalog_sub = catalog_parser.add_subparsers(dest="catalog_command")
catalog_sync_parser = catalog_sub.add_parser("sync", help="Download latest manifest.sqlite from the platform")
catalog_sync_parser.add_argument("--api-url", type=str, default=None, help="Platform API URL override")

# --- atom publish ---
atom_parser = subparsers.add_parser("atom", help="Manage atoms in the global registry")
atom_sub = atom_parser.add_subparsers(dest="atom_command")
publish_parser = atom_sub.add_parser("publish", help="Publish an atom to the global registry")
publish_parser.add_argument("path", type=str, help="Path to atom source directory")
publish_parser.add_argument("--semver", type=str, required=True, help="Semver label (e.g. 1.0.0)")

# --- bounty list ---
bounty_parser = subparsers.add_parser("bounty", help="Interact with bounties")
bounty_sub = bounty_parser.add_subparsers(dest="bounty_command")
bounty_list_parser = bounty_sub.add_parser("list", help="List open bounties")
bounty_list_parser.add_argument("--domain", type=str, default=None, help="Filter by domain tag")
bounty_list_parser.add_argument("--limit", type=int, default=20, help="Max results")

# --- bounty fund ---
bounty_fund_parser = bounty_sub.add_parser("fund", help="Fund a draft bounty")
bounty_fund_parser.add_argument("bounty_id", type=str, help="Bounty ID to fund")
```

New CLI modules:
- `sciona/commands/login_cmds.py`
- `sciona/commands/catalog_cmds.py`
- `sciona/commands/atom_cmds.py`
- `sciona/commands/bounty_cmds.py`

Each follows the pattern of existing `*_cmds.py` files: define `_cmd_*` async functions, imported into `cli.py`.

### 7. Test Strategy

**Unit tests (local, no infrastructure):**
- State machine transitions: test all valid and invalid bounty transitions, cancellation fee math.
- JWT generation/validation: mock KMS, verify round-trip encode/decode.
- SQLite snapshot generation: use in-memory SQLite (same approach as `tests/test_hyperparams.py`).
- API models: Pydantic validation of request/response schemas.
- Receipt validation: test replay prevention (wrong bounty_id rejected), signature verification with test SSH key.

**Integration tests (need local Docker, via existing `docker-compose.yml`):**
- PostgreSQL: migration scripts, CRUD operations via asyncpg.
- Memgraph: provenance upsert + query (extend existing `tests/test_graph_store.py`).
- Full API tests: use `httpx.AsyncClient` with `app=app` (FastAPI TestClient), backed by a test PostgreSQL database.

**Tests that need live services (CI only):**
- GitHub OAuth device flow: mock GitHub endpoints with `respx` or `responses`.
- S3 upload/download: use `moto` for local S3 mocking.
- Stripe checkout: mock at the HTTP layer.

**Test locations:**
- `tests/test_api_auth.py`
- `tests/test_api_registry.py`
- `tests/test_api_bounty.py`
- `tests/test_api_catalog.py`
- `tests/test_bounty_state_machine.py`
- `tests/test_snapshot_generation.py`
- `tests/test_cli_login.py`

### 8. Deployment Configuration

**Dockerfile.api** (new file, modeled on existing `Dockerfile.visualizer`):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY sciona/ sciona/
RUN pip install --no-cache-dir ".[api]"
EXPOSE 8000
CMD ["uvicorn", "sciona.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

This requires a new `[project.optional-dependencies]` group in `pyproject.toml`:

```toml
api = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "asyncpg>=0.29",
    "pyjwt[crypto]>=2.8",
    "httpx>=0.27",
    "boto3>=1.34",
    "stripe>=7.0",
]
```

**ECS Task Definition sketch:**

```json
{
  "family": "sciona-api",
  "cpu": "256",
  "memory": "512",
  "networkMode": "awsvpc",
  "containerDefinitions": [{
    "name": "api",
    "image": "${ECR_REPO}:latest",
    "portMappings": [{"containerPort": 8000}],
    "environment": [
      {"name": "SCIONA_SUPABASE_URL", "value": "..."},
      {"name": "SCIONA_MEMGRAPH_URI", "value": "bolt://memgraph.internal:7687"},
      {"name": "SCIONA_S3_BUCKET", "value": "sciona-platform"},
      {"name": "SCIONA_JWT_KMS_KEY_ID", "value": "..."}
    ],
    "secrets": [
      {"name": "SCIONA_SUPABASE_KEY", "valueFrom": "arn:aws:secretsmanager:..."},
      {"name": "GITHUB_OAUTH_CLIENT_SECRET", "valueFrom": "arn:aws:secretsmanager:..."},
      {"name": "STRIPE_SECRET_KEY", "valueFrom": "arn:aws:secretsmanager:..."}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/sciona-api",
        "awslogs-region": "us-east-1"
      }
    }
  }]
}
```

**S3 bucket structure:**

```
sciona-platform/
    atoms/{content_hash}.tar.gz
    datasets/{bounty_id}/
    receipts/{submission_id}.receipt
    manifests/manifest.sqlite
    manifests/manifest-{timestamp}.sqlite
    backups/memgraph/
    backups/postgres/
```

**docker-compose.yml additions** (for local development):

Add an `api` service alongside the existing `memgraph`, `postgres`, and `visualizer`:

```yaml
api:
  build:
    context: .
    dockerfile: Dockerfile.api
  ports:
    - "8000:8000"
  environment:
    SCIONA_MEMGRAPH_URI: bolt://memgraph:7687
    SCIONA_POSTGRES_URI: postgresql://sciona:sciona_dev@postgres:5432/sciona_architect
    SCIONA_S3_BUCKET: sciona-platform-dev
    SCIONA_JWT_PRIVATE_KEY_PATH: /app/dev_jwt_key.pem
    GITHUB_OAUTH_CLIENT_ID: ${GITHUB_OAUTH_CLIENT_ID}
    GITHUB_OAUTH_CLIENT_SECRET: ${GITHUB_OAUTH_CLIENT_SECRET}
  depends_on:
    - memgraph
    - postgres
```

### 9. Implementation Sequencing

| Step | Deliverable | Depends On | Effort |
|------|------------|------------|--------|
| B.1 | `sciona/api/models.py` -- Pydantic request/response models | Nothing | 0.5 day |
| B.2 | PostgreSQL migration SQL + asyncpg connection pool in `deps.py` | B.1 | 1 day |
| B.3 | `routers/auth.py` -- GitHub device flow + JWT | B.2 | 1.5 days |
| B.4 | `routers/registry.py` -- Atom CRUD + S3 upload | B.2, B.3 | 1.5 days |
| B.5 | SQLite snapshot generation (`api/snapshot.py`) | B.4 | 1 day |
| B.6 | `routers/catalog.py` -- search + manifest download | B.5 | 0.5 day |
| B.7 | Bounty state machine (pure logic, no API) | B.1 | 1 day |
| B.8 | `routers/bounty.py` -- full bounty lifecycle API | B.7, B.3 | 2 days |
| B.9 | CLI commands (`login`, `catalog sync`, `atom publish`, `bounty list/fund`) | B.3, B.4, B.8 | 2 days |
| B.10 | Dockerfile.api + docker-compose additions | B.8 | 0.5 day |
| B.11 | Unit + integration tests | B.1-B.9 | 2 days (parallel) |
| B.12 | ECS Fargate deployment config + Supabase setup | B.10 | 1 day |

Total: approximately 2-3 weeks.

### 10. Risks and Open Questions

1. **Supabase connection pooling from Fargate.** Supabase's built-in PgBouncer uses transaction-mode pooling, which does not support prepared statements. `asyncpg` uses prepared statements by default. Mitigation: use `asyncpg` with `statement_cache_size=0` or use Supabase's direct connection string (bypassing PgBouncer) since Fargate has persistent connections anyway.

2. **JWT key rotation.** The plan uses a single KMS key. If it is compromised, all tokens are invalid. Mitigation: support a `kid` (key ID) header in the JWT and maintain two active keys during rotation. This can be deferred to Phase C.

3. **SQLite snapshot atomicity.** If the generation crashes mid-write, the CLI could download a corrupt snapshot. Mitigation: write to a temp S3 key, then copy to the canonical key atomically (S3 PutObject is atomic; the risk is if the process crashes between SQLite write and S3 upload). Use a checksum header (`x-amz-checksum-sha256`).

4. **GitHub device flow rate limits.** GitHub rate-limits device flow polling to one request per `interval` seconds (returned in the initial response). The CLI must respect this, or users get 429 errors.

5. **Receipt SSH signature verification.** The platform needs `ssh-keygen` available in the Fargate container. The `python:3.12-slim` base image includes OpenSSH. Verify this during container testing. Alternative: use `cryptography` library to verify SSH signatures in pure Python.

6. **Bounty expiry background task.** Fargate tasks can be killed and restarted. The expiry check must be idempotent (use `UPDATE ... WHERE status = 'open' AND deadline < now()` with a returning clause). Consider using EventBridge + Lambda for reliability instead of an in-process scheduler.

7. **Multi-author contribution_share validation.** The `atom_authors` table allows shares that don't sum to 1.0. Add a database trigger or application-level check: `SELECT SUM(contribution_share) FROM atom_authors WHERE atom_id = $1` must equal 1.0 before allowing the atom to enter `approved` status.

8. **Open question: Stripe Connect vs Stripe Checkout.** For funding bounties (Principal pays escrow), Stripe Checkout works. For paying out to Architects/Originators, Stripe Connect with Express accounts is needed. The KYC trigger on first attribution maps cleanly to Stripe Connect's "deferred onboarding" pattern. This needs careful sequencing with the Payee tier upgrade.

### Critical Files for Implementation
- `/Users/conrad/personal/sciona/sciona/architect/hyperparams.py` - Contains the existing `load_hyperparams_manifest_sqlite()` function and schema that the SQLite snapshot must match exactly
- `/Users/conrad/personal/sciona/sciona/cli.py` - Main CLI entrypoint; all new commands (login, catalog, atom, bounty) must be registered here following the existing argparse pattern
- `/Users/conrad/personal/sciona/sciona/config.py` - Central config via pydantic-settings; new API config fields (supabase URL, S3 bucket, JWT key, GitHub OAuth creds) go here
- `/Users/conrad/personal/sciona/sciona/visualizer_api.py` - Existing FastAPI app pattern to follow for lifespan management, Memgraph driver init, and asyncpg setup
- `/Users/conrad/personal/sciona/pyproject.toml` - Needs new `[api]` optional dependency group and script entrypoint for the platform service
