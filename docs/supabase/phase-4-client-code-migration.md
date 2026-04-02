# Phase 4: Client / API Code Migration

## Overview

Phase 4 rewrites the FastAPI application layer to replace the custom auth stack and
raw asyncpg database access with the Supabase Python SDK. It is organized into three
parallel tracks:

| Track | Scope | Files touched |
|-------|-------|---------------|
| **F -- Auth** | JWT validation + OAuth flow | `deps.py`, `routers/auth.py`, `models.py` |
| **G -- Database Access** | asyncpg pool -> Supabase client | `deps.py`, `app.py`, all router files |
| **H -- Catalog & Manifest** | Catalog search, manifest removal, snapshot rewrite | `routers/catalog.py`, `snapshot.py` |

**Prerequisite**: Phases 1-3 complete (schema deployed, triggers active, RLS policies
applied, data backfilled). Supabase project running with GitHub OAuth provider enabled.

---

## Feature Flags for Gradual Rollout

Two environment-variable flags control which code path is active. This allows
incremental cutover without deploying separate builds.

| Flag | Default | Effect |
|---|---|---|
| `SCIONA_USE_SUPABASE_AUTH=1` | `0` (off) | `require_auth()` uses Supabase JWT validation instead of custom RS256 |
| `SCIONA_USE_SUPABASE_DB=1` | `0` (off) | Endpoints use `get_supabase()` instead of `get_db()` (asyncpg) |

**Rollout sequence**:

1. **Week 1**: Deploy with both flags off. Supabase client is initialised in lifespan
   but unused. All traffic still goes through asyncpg + custom JWT.

2. **Week 2**: Enable `SCIONA_USE_SUPABASE_AUTH=1` on staging. Exercises the Supabase
   auth path while reads/writes still hit asyncpg. Validates that Supabase JWT tokens
   are correctly issued and validated.

3. **Week 3**: Enable `SCIONA_USE_SUPABASE_DB=1` on staging. All reads and writes now
   go through supabase-py. asyncpg pool is no longer initialised (the lifespan skips
   it when the flag is set).

4. **Week 4**: Enable both flags in production. Monitor error rates, latency, and
   correctness for 48-72 hours.

5. **Week 5**: Remove the feature flags, the legacy auth code, the asyncpg dependency,
   and the `SCIONA_JWT_*` environment variables. Remove `get_db()`.

---

## Track F: Auth Migration (`deps.py` + `routers/auth.py`)

Track F replaces the custom RS256 JWT issuer and manual GitHub device flow with
Supabase Auth. After this track, the platform no longer manages cryptographic key
material for user tokens.

### F.1 Current State

`deps.py` today provides three dependency functions:

| Function | Role |
|---|---|
| `_get_jwt_public_key()` | Loads RS256 public key from env / file |
| `get_db()` | Yields an asyncpg connection from `request.app.state.db_pool` |
| `require_auth()` | Decodes custom JWT, queries `users` table via asyncpg, returns `UserRow` |

`routers/auth.py` implements the full GitHub device flow manually:
`/auth/github/device` starts the flow, `/auth/github/device/poll` exchanges the device
code for a GitHub access token, fetches the GitHub user profile, upserts into the
database, and issues a custom RS256 JWT via `_upsert_user_and_issue_jwt()`.

`UserRow` fields: `user_id`, `github_id`, `github_login`, `display_name`, `avatar_url`,
`email`, `identity_tier`, `is_blacklisted`.

### F.2 New `deps.py` Implementation

```python
"""Dependency injection for the platform API -- Supabase edition."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel


class UserProfile(BaseModel):
    """Public profile row from public.users, keyed by Supabase auth.uid()."""

    user_id: str
    github_id: int = 0
    github_login: str = ""
    display_name: str = ""
    avatar_url: str = ""
    email: str = ""
    identity_tier: str = "contributor"
    effective_tier: str = "general"
    is_blacklisted: bool = False


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

USE_SUPABASE_AUTH = os.environ.get("SCIONA_USE_SUPABASE_AUTH", "0") == "1"
USE_SUPABASE_DB = os.environ.get("SCIONA_USE_SUPABASE_DB", "0") == "1"


# ---------------------------------------------------------------------------
# Supabase client dependency
# ---------------------------------------------------------------------------

async def get_supabase(request: Request):
    """Return the Supabase async client stored on app state."""
    client = getattr(request.app.state, "supabase", None)
    if client is None:
        raise HTTPException(503, "Supabase client not available")
    return client


# ---------------------------------------------------------------------------
# Legacy asyncpg dependency (kept during dual-run period)
# ---------------------------------------------------------------------------

async def get_db(request: Request):
    """Yield an asyncpg connection. Removed after full cutover."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Database not available")
    async with pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Auth: Supabase JWT validation
# ---------------------------------------------------------------------------

async def _require_auth_supabase(request: Request) -> UserProfile:
    """Validate Supabase JWT and return the user's public profile."""
    supabase = await get_supabase(request)

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Missing authorization token")

    # Supabase SDK validates the JWT and returns the auth.users row
    try:
        user_response = await supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token -- run `sciona login`")

    if not user_response or not user_response.user:
        raise HTTPException(401, "Invalid token")

    uid = user_response.user.id

    # Fetch public.users profile (one PostgREST call, RLS-safe)
    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(uid))
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(401, "User profile not found")
    if result.data.get("is_blacklisted"):
        raise HTTPException(403, "Account suspended")

    return UserProfile(**result.data)


async def _require_auth_legacy(request: Request) -> UserProfile:
    """Legacy JWT validation -- kept during transition."""
    from fastapi.security import HTTPBearer

    bearer = HTTPBearer()
    credentials = await bearer(request)

    try:
        import jwt as pyjwt
    except ImportError:
        raise HTTPException(503, "PyJWT not installed")

    public_key = os.environ.get("SCIONA_JWT_PUBLIC_KEY", "")
    if not public_key:
        key_path = os.environ.get("SCIONA_JWT_PUBLIC_KEY_PATH", "")
        if key_path and os.path.exists(key_path):
            with open(key_path) as f:
                public_key = f.read()
    if not public_key:
        raise HTTPException(503, "JWT public key not configured")

    token = credentials.credentials
    try:
        payload = pyjwt.decode(token, public_key, algorithms=["RS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired -- run `sciona login`")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    db = getattr(request.app.state, "db_pool", None)
    if db is None:
        raise HTTPException(503, "Database not available")

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1::uuid", payload["sub"]
        )

    if not row:
        raise HTTPException(401, "User not found")
    if row["is_blacklisted"]:
        raise HTTPException(403, "Account suspended")

    return UserProfile(**dict(row))


async def require_auth(request: Request) -> UserProfile:
    """Route to Supabase or legacy auth based on feature flag."""
    if USE_SUPABASE_AUTH:
        return await _require_auth_supabase(request)
    return await _require_auth_legacy(request)
```

### F.3 UserRow to UserProfile Mapping

The old `UserRow` is replaced by `UserProfile`, which adds `effective_tier` from the
new schema. All downstream router code that currently type-hints `UserRow` must be
updated to `UserProfile`. The field names are intentionally identical for the shared
fields so that dict-spreading patterns continue to work.

| Old `UserRow` field | New `UserProfile` field | Notes |
|---|---|---|
| `user_id: str` | `user_id: str` | Now a UUID string from `auth.uid()` |
| `github_id: int` | `github_id: int` | Populated by `handle_new_user()` trigger |
| `github_login: str` | `github_login: str` | Same |
| `display_name: str` | `display_name: str` | Same |
| `avatar_url: str` | `avatar_url: str` | Same |
| `email: str` | `email: str` | Same |
| `identity_tier: str` | `identity_tier: str` | Payout identity, not access control |
| `is_blacklisted: bool` | `is_blacklisted: bool` | Same |
| _(none)_ | `effective_tier: str` | **New**: materialized entitlement tier for catalog visibility |

### F.4 Auth Router Rewrite (`routers/auth.py`)

**Current state.** Three endpoints:
- `GET /auth/github/device` -- starts GitHub device flow via direct GitHub API calls
- `POST /auth/github/device/poll` -- polls for device code completion, exchanges for
  GitHub access token, calls `_upsert_user_and_issue_jwt()` to mint a custom RS256 JWT
- `GET /auth/me` -- returns current user from `require_auth`

Plus the private helper `_upsert_user_and_issue_jwt()` that loads the RS256 private
key, upserts the user row, and signs a JWT.

**Target state.** Supabase Auth handles the full OAuth lifecycle. Two auth flows are
supported:

**Option A -- PKCE browser redirect (web clients):**
Replace the device flow endpoints with a single `GET /auth/login` that returns the
Supabase OAuth URL. The frontend opens a browser, Supabase handles the GitHub OAuth
callback, and the client receives a Supabase session token.

```python
@router.get("/auth/login")
async def login_redirect(supabase=Depends(get_supabase)) -> dict:
    """Return Supabase GitHub OAuth URL for browser-based login."""
    result = await supabase.auth.sign_in_with_oauth(
        {"provider": "github", "options": {"redirect_to": CALLBACK_URL}}
    )
    return {"url": result.url}
```

**Option B -- Server-mediated device flow (headless CLI, `sciona login`):**
Keep the device flow UX but replace the JWT issuance step. After the GitHub access
token is obtained, call `supabase.auth.sign_in_with_id_token()` to create/sign-in the
user via Supabase Auth, returning a Supabase session instead of a custom JWT.

```python
@router.post("/auth/github/device/poll")
async def github_device_poll(
    device_code: str,
    supabase=Depends(get_supabase),
) -> TokenResponse | PendingResponse:
    # ... existing GitHub device code exchange (unchanged) ...
    github_token = data["access_token"]

    # Exchange GitHub token for Supabase session
    session = await supabase.auth.sign_in_with_id_token({
        "provider": "github",
        "token": github_token,
    })
    if not session or not session.session:
        raise HTTPException(500, "Failed to create Supabase session")

    return TokenResponse(
        access_token=session.session.access_token,
        refresh_token=session.session.refresh_token,
        expires_in=session.session.expires_in,
    )
```

The `TokenResponse` model gains a `refresh_token` field. The CLI stores both tokens
and uses the refresh token to renew sessions transparently.

**`/auth/me` rewrite:**

```python
@router.get("/auth/me")
async def get_me(user: UserProfile = Depends(require_auth)) -> UserResponse:
    return UserResponse(
        user_id=UUID(user.user_id),
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        identity_tier=user.identity_tier,
        effective_tier=user.effective_tier,
        reputation_score=0,
        created_at=datetime.now(timezone.utc),
    )
```

**Endpoints and code removed after cutover:**
- `_get_jwt_public_key()` -- no longer needed
- `_upsert_user_and_issue_jwt()` -- Supabase handles user creation via trigger
- `DeviceFlowResponse` and `PendingResponse` models (if using Option A only)
- `SCIONA_JWT_PUBLIC_KEY` / `SCIONA_JWT_PRIVATE_KEY` env vars

### F.5 Update FastAPI Lifespan (`app.py`)

The app lifespan currently initialises the asyncpg pool on `app.state.db_pool`.
During the transition it initialises both; after cutover only Supabase remains.

```python
from supabase import acreate_client

@asynccontextmanager
async def _lifespan(app: FastAPI):
    db_pool = None
    supabase_client = None

    # Supabase client (new)
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if supabase_url and supabase_key:
        supabase_client = await acreate_client(supabase_url, supabase_key)
        app.state.supabase = supabase_client

    # Legacy asyncpg pool (kept during transition, removed at full cutover)
    postgres_uri = os.environ.get("SCIONA_POSTGRES_URI", "")
    if postgres_uri and os.environ.get("SCIONA_USE_SUPABASE_DB") != "1":
        import asyncpg
        db_pool = await asyncpg.create_pool(
            postgres_uri, min_size=2, max_size=10, statement_cache_size=0,
        )
        app.state.db_pool = db_pool

    # Memgraph (unchanged)
    graph_driver = None
    memgraph_uri = os.environ.get("SCIONA_MEMGRAPH_URI", "")
    if memgraph_uri:
        try:
            from neo4j import AsyncGraphDatabase
            graph_driver = AsyncGraphDatabase.driver(memgraph_uri, auth=None)
            app.state.graph_driver = graph_driver
        except Exception:
            graph_driver = None

    yield

    if graph_driver is not None:
        try:
            await graph_driver.close()
        except Exception:
            pass
    if db_pool is not None:
        try:
            await db_pool.close()
        except Exception:
            pass
```

The service-role client (for admin operations like user creation) should be a separate
instance stored on `app.state.supabase_admin` if you need to distinguish between
user-context and admin-context calls.

---

## Track G: API Endpoint Migration (asyncpg to supabase-py)

### G.1 General Pattern

Every router currently receives a raw asyncpg connection via `db=Depends(get_db)` and
executes parameterized SQL. The migration replaces this with the supabase-py PostgREST
query builder or `supabase.rpc()` for complex queries.

**asyncpg pattern:**
```python
row = await db.fetchrow("SELECT * FROM atoms WHERE fqdn = $1", fqdn)
```

**supabase-py pattern:**
```python
result = await supabase.table("atoms").select("*").eq("fqdn", fqdn).single().execute()
row = result.data
```

**Translation table:**

| asyncpg | supabase-py |
|---|---|
| `db.fetchrow(sql, *params)` | `.select(...).eq(...).single().execute()` -- `.data` is dict |
| `db.fetch(sql, *params)` | `.select(...).eq(...).execute()` -- `.data` is list |
| `db.fetchval(sql, *params)` | `.select("col").eq(...).single().execute()` -- `.data["col"]` |
| `db.execute(sql, *params)` | `.update({...}).eq(...).execute()` |
| `INSERT ... RETURNING *` | `.insert({...}).execute()` -- `.data[0]` is the returned row |
| `$1::uuid` casting | Not needed; PostgREST handles type coercion |
| `$N = ANY(array_col)` | `.contains("array_col", [value])` |
| `ILIKE '%q%'` | `.ilike("col", f"%{q}%")` |
| `LIMIT $N OFFSET $M` | `.range(offset, offset + limit - 1)` |
| `COUNT(*)` | `.select("*", count="exact")` -- count in `result.count` |
| Raw SQL joins | Views, RPCs, or PostgREST resource embedding |

### G.2 PostgREST Limitations to Watch

These are concrete traps that will surface during the migration:

**1. JSONB `?` operator not supported in PostgREST filters.**

The `originator_impact` view and the dashboard h-index query use
`s.atom_versions ? a.fqdn` (JSONB key-exists). PostgREST cannot filter on this
operator.

**Fix**: Rewrite as a Postgres function exposed via `supabase.rpc()`:

```sql
CREATE OR REPLACE FUNCTION public.get_originator_impact(p_user_id UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT jsonb_build_object(
    'originator_id', aa.user_id,
    'github_login', u.github_login,
    'bounty_count', COUNT(DISTINCT b.bounty_id),
    'total_bounty_value', COALESCE(SUM(b.escrow_amount), 0),
    'atom_count', COUNT(DISTINCT aa.atom_id)
  )
  FROM public.atom_authors aa
  JOIN public.users u ON u.user_id = aa.user_id
  JOIN public.atoms a ON a.atom_id = aa.atom_id
  LEFT JOIN public.submissions s ON s.atom_versions ? a.fqdn AND s.is_winner = true
  LEFT JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled'
  WHERE aa.user_id = p_user_id
  GROUP BY aa.user_id, u.github_login;
$$;
```

Called as: `await supabase.rpc("get_originator_impact", {"p_user_id": str(uid)}).execute()`

**2. `tsvector` full-text search filter syntax.**

PostgREST supports full-text search via the `.text_search()` method but the column
must be `tsvector` type (which `catalog_atoms_index.search_document` is). Usage:

```python
result = await (
    supabase.table("catalog_atoms_index")
    .select("*")
    .text_search("search_document", query, type="websearch")
    .execute()
)
```

`type="websearch"` allows natural-language query syntax. For `plainto_tsquery`
semantics use `type="plain"`. For `phraseto_tsquery` use `type="phrase"`.

**3. No `DISTINCT ON` in PostgREST.**

The `atom_audit_latest` materialized view already handles this server-side. Do not
attempt to replicate `DISTINCT ON` via PostgREST filters.

**4. Array containment vs. membership.**

`$1 = ANY(domain_tags)` becomes `.contains("domain_tags", [value])` in supabase-py.
Note: `.contains()` checks that the column array contains ALL provided values. For
"any of these tags" semantics, use `.overlaps()` instead:
```python
.overlaps("domain_tags", [tag1, tag2])
```

**5. JSONB column reads return Python dicts automatically.**

No need for `json.loads()` when reading `config_yml`, `receipt_json`, etc. When
writing, pass Python dicts directly -- the SDK serializes them.

**6. No server-side `SELECT ... FOR UPDATE`.**

PostgREST does not support row-level locking. For race-sensitive operations (bounty
state transitions), use an RPC with explicit `FOR UPDATE` or rely on optimistic
concurrency with a version column.

**7. `INSERT ... RETURNING *` behavior.**

supabase-py `.insert()` returns the inserted row by default (PostgREST
`Prefer: return=representation`). Access via `result.data[0]`.

**8. `now()` not evaluated in PostgREST values.**

PostgREST does not evaluate SQL functions in INSERT/UPDATE values. For
`updated_at = now()`, either rely on a database trigger that sets `updated_at` on
UPDATE (preferred, already standard in the Supabase schema), or pass
`datetime.now(timezone.utc).isoformat()` from the application.

### G.3 Example Endpoint Rewrites

#### G.3.1 `routers/catalog.py` -- Search

**Before** (asyncpg, raw SQL):
```python
@router.get("/search")
async def catalog_search(q: str, domain_tag: str | None = None,
                         limit: int = Query(default=50, le=200),
                         db=Depends(get_db)) -> list[CatalogEntry]:
    conditions = ["a.status = 'approved'"]
    params: list = []
    idx = 1
    if q:
        conditions.append(f"(a.fqdn ILIKE ${idx} OR a.description ILIKE ${idx})")
        params.append(f"%{q}%")
        idx += 1
    # ... manual SQL assembly ...
    rows = await db.fetch(f"SELECT ... WHERE {where} ...", *params)
```

**After** (supabase-py, PostgREST with full-text search):
```python
@router.get("/search")
async def catalog_search(
    q: str = "",
    domain_tag: str | None = None,
    mode: str = Query(default="fts", regex="^(fts|ilike)$"),
    limit: int = Query(default=50, le=200),
    supabase=Depends(get_supabase),
) -> list[CatalogEntry]:
    """Full-text or ILIKE search against the catalog index."""
    query = supabase.table("catalog_atoms_index").select(
        "fqdn, technical_description, domain_tags"
    )

    if mode == "fts" and q:
        query = query.text_search("search_document", q, type="websearch")
    elif q:
        query = query.or_(f"fqdn.ilike.%{q}%,technical_description.ilike.%{q}%")

    if domain_tag:
        query = query.contains("domain_tags", [domain_tag])

    query = query.limit(limit)
    result = await query.execute()

    return [
        CatalogEntry(
            fqdn=r["fqdn"],
            description=r["technical_description"],
            domain_tags=r["domain_tags"],
        )
        for r in result.data
    ]
```

#### G.3.2 `routers/registry.py` -- Atom Publish

**Before** (asyncpg):
```python
atom_row = await db.fetchrow("SELECT atom_id FROM atoms WHERE fqdn = $1", body.fqdn)
if atom_row is None:
    atom_row = await db.fetchrow(
        "INSERT INTO atoms (fqdn, owner_id, ...) VALUES ($1, $2::uuid, ...) RETURNING atom_id",
        body.fqdn, user.user_id, ...
    )
atom_id = atom_row["atom_id"]
await db.execute("UPDATE atom_versions SET is_latest = FALSE WHERE atom_id = $1", atom_id)
version_row = await db.fetchrow("INSERT INTO atom_versions (...) VALUES (...) RETURNING version_id", ...)
```

**After** (supabase-py):
```python
# Check existing
existing = await (
    supabase.table("atoms")
    .select("atom_id")
    .eq("fqdn", body.fqdn)
    .maybe_single()
    .execute()
)

if existing.data is None:
    inserted = await (
        supabase.table("atoms")
        .insert({
            "fqdn": body.fqdn,
            "owner_id": user.user_id,
            "domain_tags": body.domain_tags,
            "description": body.description,
        })
        .execute()
    )
    atom_id = inserted.data[0]["atom_id"]
    is_new = True
else:
    atom_id = existing.data["atom_id"]
    is_new = False

# Mark previous versions non-latest
await (
    supabase.table("atom_versions")
    .update({"is_latest": False})
    .eq("atom_id", atom_id)
    .execute()
)

# Insert new version
version_result = await (
    supabase.table("atom_versions")
    .insert({
        "atom_id": atom_id,
        "content_hash": content_hash,
        "semver": body.semver,
        "is_latest": True,
        "s3_key": s3_key,
        "fingerprint": body.fingerprint,
    })
    .execute()
)
version_id = version_result.data[0]["version_id"]
```

**Note**: Atom publish should now also insert rows into `atom_io_specs`,
`atom_parameters`, `atom_descriptions`, and `atom_references` if the publish request
includes that data. Extend `AtomPublishRequest` accordingly.

#### G.3.3 `routers/bounty.py` -- List Bounties with Filters

**Before** (asyncpg, manual SQL filter assembly):
```python
conditions = ["1=1"]
params: list = []
idx = 1
if status:
    conditions.append(f"b.status = ${idx}")
    params.append(status)
    idx += 1
where = " AND ".join(conditions)
count_row = await db.fetchrow(f"SELECT COUNT(*) AS cnt FROM bounties b WHERE {where}", *params)
rows = await db.fetch(f"SELECT ... FROM bounties b WHERE {where} ... LIMIT ${idx} OFFSET ${idx+1}", *params)
```

**After** (supabase-py):
```python
@router.get("")
async def list_bounties(
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(get_supabase),
) -> PaginatedResponse:
    query = supabase.table("bounties").select(
        "bounty_id, title, escrow_amount, status, deadline, tier, created_at",
        count="exact",
    )

    if status:
        query = query.eq("status", status)

    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = await query.execute()

    items = [
        BountySummaryResponse(
            bounty_id=r["bounty_id"],
            title=r["title"],
            escrow_amount=float(r["escrow_amount"]),
            status=r["status"],
            deadline=r["deadline"],
            tier=r["tier"],
        )
        for r in result.data
    ]

    return PaginatedResponse(
        items=items, total=result.count or 0, limit=limit, offset=offset
    )
```

#### G.3.4 `routers/dashboard.py` -- Originator Impact (JSONB `?` workaround)

**Before** (asyncpg, raw SQL with JSONB `?`):
```python
row = await db.fetchrow(
    "SELECT * FROM originator_impact WHERE originator_id = $1", originator_id
)
bounty_rows = await db.fetch(
    """SELECT b.escrow_amount
       FROM atom_authors aa
       JOIN atoms a ON a.atom_id = aa.atom_id
       JOIN submissions s ON s.atom_versions ? a.fqdn AND s.is_winner = true
       JOIN bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled'
       WHERE aa.user_id = $1""",
    originator_id,
)
```

**After** (supabase-py via RPC):
```python
@router.get("/dashboard/originator/{originator_id}/impact")
async def get_originator_impact(
    originator_id: UUID,
    supabase=Depends(get_supabase),
) -> dict:
    # Use the server-side RPC that handles the JSONB ? join
    result = await supabase.rpc(
        "get_originator_impact", {"p_user_id": str(originator_id)}
    ).execute()

    if not result.data:
        raise HTTPException(404, "Originator not found")

    data = result.data
    # Extract individual bounty values for h-index from a separate RPC
    bounty_result = await supabase.rpc(
        "get_originator_bounty_values", {"p_user_id": str(originator_id)}
    ).execute()
    bounty_values = [float(r["escrow_amount"]) for r in (bounty_result.data or [])]

    impact = compute_impact_factor(
        bounty_values,
        atom_count=data.get("atom_count", 0),
        originator_id=str(originator_id),
        github_username=data.get("github_login", ""),
    )
    return impact.model_dump()
```

#### G.3.5 `routers/verification.py` -- Submission Status

**Before** (asyncpg):
```python
row = await db.fetchrow("SELECT * FROM submissions WHERE submission_id = $1", submission_id)
runs = await db.fetch(
    "SELECT status, metric_values, ... FROM verification_runs WHERE submission_id = $1 ...",
    submission_id,
)
```

**After** (supabase-py):
```python
@router.get("/submissions/{submission_id}/status")
async def get_submission_status(
    submission_id: UUID,
    supabase=Depends(get_supabase),
) -> dict:
    sub_result = await (
        supabase.table("submissions")
        .select("*")
        .eq("submission_id", str(submission_id))
        .single()
        .execute()
    )
    if not sub_result.data:
        raise HTTPException(404, "Submission not found")

    runs_result = await (
        supabase.table("verification_runs")
        .select("status, metric_values, output_hash, is_deterministic")
        .eq("submission_id", str(submission_id))
        .order("created_at", desc=True)
        .execute()
    )

    return {
        "submission_id": str(submission_id),
        "verification_status": sub_result.data["verification_status"],
        "runs": runs_result.data,
    }
```

#### G.3.6 `routers/dashboard.py` -- Atom Benchmarks (multi-table JOIN)

**Before** (asyncpg):
```python
rows = await db.fetch(
    """SELECT ab.benchmark_name, ab.metric_name, ab.metric_value,
              ab.dataset_tag, ab.measured_at
       FROM atom_benchmarks ab
       JOIN atom_versions av ON av.version_id = ab.version_id
       JOIN atoms a ON a.atom_id = av.atom_id
       WHERE a.fqdn = $1
       ORDER BY ab.measured_at DESC""",
    fqdn,
)
```

**After** (supabase-py via RPC):
```python
result = await supabase.rpc("get_atom_benchmarks", {"p_fqdn": fqdn}).execute()
return result.data or []
```

Supporting RPC:
```sql
CREATE OR REPLACE FUNCTION public.get_atom_benchmarks(p_fqdn TEXT)
RETURNS TABLE (
    benchmark_name TEXT,
    metric_name TEXT,
    metric_value DOUBLE PRECISION,
    dataset_tag TEXT,
    measured_at TIMESTAMPTZ
)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = '' AS $$
    SELECT ab.benchmark_name, ab.metric_name, ab.metric_value,
           ab.dataset_tag, ab.measured_at
    FROM public.atom_benchmarks ab
    JOIN public.atom_versions av ON av.version_id = ab.version_id
    JOIN public.atoms a ON a.atom_id = av.atom_id
    WHERE a.fqdn = p_fqdn
    ORDER BY ab.measured_at DESC;
$$;
```

### G.4 Full RPC Inventory

Track G introduces several server-side RPCs to replace complex multi-table JOINs
that cannot be expressed efficiently through PostgREST filters:

| RPC name | Purpose | Called from |
|----------|---------|------------|
| `get_atom_document(fqdn)` | Full atom detail bundle (Section 2.8.1 of migration plan) | `registry.py`, `catalog.py` |
| `get_bounty_leaderboard(bounty_id, limit, offset)` | Verified submissions ranking | `verification.py` |
| `get_atom_benchmarks(fqdn)` | Benchmark results for atom | `dashboard.py` |
| `get_originator_impact(user_id)` | Impact factor data (JSONB `?`) | `dashboard.py` |
| `get_originator_bounty_values(user_id)` | Individual bounty values for h-index | `dashboard.py` |
| `get_manifest_benchmarks()` | Benchmarks with atom FQDN for SQLite manifest | `snapshot.py` |
| `catalog_search_fts(query, domain_tag, limit)` | FTS on catalog_atoms_index | `catalog.py` |

These RPCs should be created as part of a Phase 4 schema migration so they are
available when the code changes land. The SQL for `get_atom_document` is already
defined in Section 2.8.1 of the migration plan.

---

## Track H: SQLite Manifest Generator Rewrite (`snapshot.py`)

### H.1 Current State

`snapshot.py` exposes `generate_manifest_sqlite(atoms, hyperparams, benchmarks)` which
accepts pre-fetched lists of dicts and writes them into a SQLite database with three
tables: `atoms`, `hyperparams`, `benchmarks`.

The data is currently fetched by the caller using asyncpg. The `/catalog/manifest`
endpoint in `routers/catalog.py` serves a pre-built SQLite file from disk.

### H.2 New Implementation

The rewritten `snapshot.py` fetches data directly from Supabase, adds new documentation
tables to the SQLite schema, and removes the dependency on a pre-built file.

```python
"""SQLite manifest snapshot -- generates local cache from Supabase."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


async def fetch_manifest_data(supabase) -> dict[str, list[dict]]:
    """Fetch all data needed for the manifest from Supabase.

    Uses the service role client so RLS is bypassed -- the manifest
    includes all approved publishable atoms regardless of caller tier.
    """
    # Atoms: only approved, publishable
    atoms_result = await (
        supabase.table("atoms")
        .select(
            "atom_id, fqdn, status, domain_tags, description, "
            "visibility_tier, source_kind, stateful_kind, "
            "is_stochastic, is_ffi, namespace_path, source_package"
        )
        .eq("status", "approved")
        .eq("is_publishable", True)
        .execute()
    )

    atom_ids = [a["atom_id"] for a in atoms_result.data]

    # Hyperparams for those atoms
    hp_result = await (
        supabase.table("hyperparams")
        .select("*")
        .in_("atom_id", atom_ids)
        .execute()
    )

    # Benchmarks via RPC (requires join across three tables)
    bm_result = await supabase.rpc("get_manifest_benchmarks").execute()

    # Audit rollups
    rollup_result = await (
        supabase.table("atom_audit_rollups")
        .select("atom_id, overall_verdict, risk_tier, risk_score, "
                "acceptability_score, trust_readiness")
        .in_("atom_id", atom_ids)
        .execute()
    )

    # Dejargonized descriptions
    desc_result = await (
        supabase.table("atom_descriptions")
        .select("atom_id, content, jargon_score")
        .eq("kind", "dejargonized")
        .eq("language", "en")
        .in_("atom_id", atom_ids)
        .execute()
    )

    return {
        "atoms": atoms_result.data,
        "hyperparams": hp_result.data,
        "benchmarks": bm_result.data if bm_result.data else [],
        "rollups": rollup_result.data,
        "descriptions": desc_result.data,
    }


def generate_manifest_sqlite(
    data: dict[str, list[dict]],
    output_path: Path | None = None,
) -> sqlite3.Connection:
    """Build the manifest.sqlite from Supabase-fetched data.

    The schema is extended from the original to include audit rollups
    and dejargonized descriptions. Pipeline-hot readers
    (load_hyperparams_manifest_sqlite, load_benchmarks_sqlite) read
    only the atoms/hyperparams/benchmarks tables and are unaffected.
    """
    db_str = str(output_path) if output_path else ":memory:"
    con = sqlite3.connect(db_str)

    con.executescript("""
        CREATE TABLE IF NOT EXISTS atoms (
            atom_id        TEXT PRIMARY KEY,
            fqdn           TEXT UNIQUE NOT NULL,
            status         TEXT NOT NULL DEFAULT 'approved',
            domain_tags    TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL DEFAULT '',
            visibility_tier TEXT NOT NULL DEFAULT 'general',
            source_kind    TEXT NOT NULL DEFAULT 'hand_written',
            stateful_kind  TEXT NOT NULL DEFAULT 'none',
            is_stochastic  INTEGER NOT NULL DEFAULT 0,
            is_ffi         INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS hyperparams (
            hp_id             TEXT PRIMARY KEY,
            atom_id           TEXT NOT NULL,
            name              TEXT NOT NULL,
            kind              TEXT NOT NULL,
            default_value     TEXT,
            min_value         TEXT,
            max_value         TEXT,
            step_value        TEXT,
            log_scale         INTEGER NOT NULL DEFAULT 0,
            choices_json      TEXT,
            constraints_json  TEXT,
            semantic_role     TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'approved',
            UNIQUE (atom_id, name)
        );

        CREATE TABLE IF NOT EXISTS benchmarks (
            atom_fqdn       TEXT NOT NULL,
            content_hash    TEXT NOT NULL,
            benchmark_name  TEXT NOT NULL,
            metric_name     TEXT NOT NULL,
            metric_value    REAL NOT NULL,
            dataset_tag     TEXT NOT NULL DEFAULT '',
            measured_at     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (atom_fqdn, content_hash, benchmark_name, metric_name)
        );

        CREATE TABLE IF NOT EXISTS audit_rollups (
            atom_id           TEXT PRIMARY KEY,
            overall_verdict   TEXT NOT NULL DEFAULT 'unknown',
            risk_tier         TEXT NOT NULL DEFAULT 'medium',
            risk_score        INTEGER NOT NULL DEFAULT 0,
            acceptability_score INTEGER NOT NULL DEFAULT 0,
            trust_readiness   TEXT NOT NULL DEFAULT 'not_ready'
        );

        CREATE TABLE IF NOT EXISTS descriptions (
            atom_id        TEXT PRIMARY KEY,
            content        TEXT NOT NULL DEFAULT '',
            jargon_score   REAL NOT NULL DEFAULT 1.0
        );
    """)

    for atom in data.get("atoms", []):
        tags = atom.get("domain_tags", [])
        tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
        con.execute(
            "INSERT OR REPLACE INTO atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(atom["atom_id"]),
                atom["fqdn"],
                atom.get("status", "approved"),
                tags_str,
                atom.get("description", ""),
                atom.get("visibility_tier", "general"),
                atom.get("source_kind", "hand_written"),
                atom.get("stateful_kind", "none"),
                int(atom.get("is_stochastic", False)),
                int(atom.get("is_ffi", False)),
            ),
        )

    for hp in data.get("hyperparams", []):
        con.execute(
            """INSERT OR REPLACE INTO hyperparams
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(hp.get("hp_id", "")),
                str(hp["atom_id"]),
                hp["name"],
                hp["kind"],
                _str_or_none(hp.get("default_value")),
                _str_or_none(hp.get("min_value")),
                _str_or_none(hp.get("max_value")),
                _str_or_none(hp.get("step_value")),
                int(hp.get("log_scale", False)),
                _str_or_none(hp.get("choices_json")),
                _str_or_none(hp.get("constraints_json")),
                hp.get("semantic_role", ""),
                hp.get("status", "approved"),
            ),
        )

    for bm in data.get("benchmarks", []):
        con.execute(
            "INSERT OR REPLACE INTO benchmarks VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                bm["atom_fqdn"],
                bm["content_hash"],
                bm["benchmark_name"],
                bm["metric_name"],
                bm["metric_value"],
                bm.get("dataset_tag", ""),
                bm.get("measured_at", ""),
            ),
        )

    for rollup in data.get("rollups", []):
        con.execute(
            "INSERT OR REPLACE INTO audit_rollups VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(rollup["atom_id"]),
                rollup.get("overall_verdict", "unknown"),
                rollup.get("risk_tier", "medium"),
                rollup.get("risk_score", 0),
                rollup.get("acceptability_score", 0),
                rollup.get("trust_readiness", "not_ready"),
            ),
        )

    for desc in data.get("descriptions", []):
        con.execute(
            "INSERT OR REPLACE INTO descriptions VALUES (?, ?, ?)",
            (
                str(desc["atom_id"]),
                desc.get("content", ""),
                desc.get("jargon_score", 1.0),
            ),
        )

    con.commit()
    return con


def _str_or_none(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)
```

### H.3 Supporting RPC for Benchmarks

The benchmarks query requires a join across `atom_benchmarks`, `atom_versions`, and
`atoms`. This is not expressible via PostgREST filters. Add a server-side function:

```sql
CREATE OR REPLACE FUNCTION public.get_manifest_benchmarks()
RETURNS TABLE (
    atom_fqdn TEXT,
    content_hash TEXT,
    benchmark_name TEXT,
    metric_name TEXT,
    metric_value DOUBLE PRECISION,
    dataset_tag TEXT,
    measured_at TEXT
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT
        a.fqdn AS atom_fqdn,
        av.content_hash,
        ab.benchmark_name,
        ab.metric_name,
        ab.metric_value,
        ab.dataset_tag,
        ab.measured_at::text
    FROM public.atom_benchmarks ab
    JOIN public.atom_versions av ON av.version_id = ab.version_id
    JOIN public.atoms a ON a.atom_id = av.atom_id
    WHERE a.status = 'approved'
      AND a.is_publishable = TRUE;
$$;
```

### H.4 `/catalog/manifest` Endpoint Removal

The `/catalog/manifest` endpoint is removed from `routers/catalog.py`. The CLI command
`sciona catalog sync` is updated to call `fetch_manifest_data()` +
`generate_manifest_sqlite()` directly instead of downloading a pre-built file.

Remove:
- The `download_manifest()` function from `routers/catalog.py`
- The `SCIONA_MANIFEST_PATH` environment variable from deployment config
- Any CI/cron job that pre-builds `manifest.sqlite` for the old endpoint

### H.5 Add `/catalog/atom/{fqdn}` Endpoint

New endpoint for fetching the full atom document bundle:

```python
@router.get("/atom/{fqdn:path}")
async def get_atom_document_endpoint(
    fqdn: str,
    supabase=Depends(get_supabase),
) -> dict:
    """Full atom document bundle assembled server-side."""
    result = await supabase.rpc("get_atom_document", {"request_fqdn": fqdn}).execute()
    if not result.data:
        raise HTTPException(404, f"Atom {fqdn!r} not found")
    return result.data
```

### H.6 Downstream Reader Compatibility

These functions continue to read from `~/.sciona/manifest.sqlite` unchanged:

| Function | Location | Tables read |
|---|---|---|
| `load_hyperparams_manifest_sqlite()` | `sciona/architect/hyperparams.py` | `atoms`, `hyperparams` |
| `load_benchmarks_sqlite()` | `sciona/principal/benchmark_priors.py` | `atoms`, `benchmarks` |
| `parse_manifest_sqlite()` | `sciona/ecosystem/webhook_sync.py` | `atoms` |

The new `audit_rollups` and `descriptions` tables in the SQLite schema are additive.
Existing readers do not query them and are therefore unaffected.

### H.7 Update `sciona catalog sync`

The CLI command `sciona catalog sync` currently downloads from
`GET /catalog/manifest`. It must be updated to:

1. Authenticate with Supabase (pass the stored session token).
2. Call the rewritten `fetch_manifest_data()` + `generate_manifest_sqlite()` from
   `snapshot.py`, passing the Supabase client.
3. Write to `~/.sciona/manifest.sqlite`.

---

## Parallelism

Tracks F, G, and H can proceed in parallel with the following constraints:

```
Track F (Auth)
  |
  +---> F.1-F.2 (deps.py rewrite) ----+
  |                                     |
  +---> F.4 (auth.py rewrite)          |
  |                                     |
  +---> F.5 (lifespan setup) ----------+---> merge: get_supabase() available
                                        |
Track G (Database Access)               |
  |                                     |
  +---> G.4 (create RPCs) <-----------+ (can start immediately)
  |                                     |
  +---> G.3.1 (catalog.py)   \         |
  +---> G.3.2 (registry.py)   |        |
  +---> G.3.3 (bounty.py)     |--- these can all run in parallel
  +---> G.3.4 (dashboard.py)  |    once F.5 is done (get_supabase available)
  +---> G.3.5 (verification.py) /
                                        |
Track H (Catalog & Manifest)            |
  |                                     |
  +---> H.4 (remove manifest endpoint) --- independent, can start immediately
  +---> H.2 (snapshot.py rewrite) --- depends on get_manifest_benchmarks RPC
  +---> H.5 (atom document endpoint) --- depends on get_atom_document RPC
  +---> H.7 (catalog sync update) --- depends on H.2
```

**Recommended execution order:**
1. F.5 + G.4 (RPCs) in parallel -- unblocks everything else
2. F.1-F.2 + F.4 + G.3.* + H.4 in parallel
3. H.2 + H.5 (depend on RPCs from G.4)
4. H.7 last (depends on H.2)

---

## Testing Strategy

### Track F Tests (Auth)

| Test | Description |
|---|---|
| `test_require_auth_supabase_valid_token` | Mock `supabase.auth.get_user()` to return a valid user. Assert `UserProfile` is returned with correct fields. |
| `test_require_auth_supabase_expired_token` | Mock `get_user()` to raise. Assert 401. |
| `test_require_auth_supabase_blacklisted` | Mock `get_user()` success + `table("users").select()` returns `is_blacklisted=True`. Assert 403. |
| `test_require_auth_supabase_no_profile` | Mock `get_user()` success + profile query returns None. Assert 401. |
| `test_require_auth_legacy_still_works` | With `USE_SUPABASE_AUTH=0`, verify the old RS256 path works unchanged. |
| `test_device_flow_returns_supabase_session` | Mock GitHub token exchange + `supabase.auth.sign_in_with_id_token()`. Assert response contains `access_token` and `refresh_token`. |
| `test_auth_me_returns_effective_tier` | Assert `/auth/me` response includes `effective_tier`. |

### Track G Tests (Endpoint Migration)

| Test | Description |
|---|---|
| `test_catalog_search_fts` | Mock `supabase.table("catalog_atoms_index").select().text_search()`. Assert correct results shape. |
| `test_catalog_search_ilike_fallback` | With `mode=ilike`, assert `.or_()` filter is used instead of `.text_search()`. |
| `test_publish_atom_supabase` | Mock insert chain. Assert atom + version rows created, `is_latest` set correctly. |
| `test_list_bounties_with_status_filter` | Mock `.eq("status", "open")`. Assert pagination metadata correct. |
| `test_originator_impact_rpc` | Mock `supabase.rpc("get_originator_impact")`. Assert impact factor computation. |
| `test_submission_status_supabase` | Mock two table queries. Assert response shape matches legacy. |
| `test_bounty_create_supabase` | Mock `.insert()`. Assert RLS-compatible fields (`principal_id = user_id`). |
| `test_bounty_cancel_supabase` | Verify cancellation still computes fee correctly via supabase-py reads/writes. |

**Regression approach**: Run the existing test suite against both code paths. The
feature flag makes A/B comparison trivial -- run each test twice, once with the flag
on and once off, comparing response shapes.

### Track H Tests (Snapshot)

| Test | Description |
|---|---|
| `test_fetch_manifest_data` | Mock all Supabase queries. Assert returned dict has all 5 expected keys. |
| `test_generate_manifest_sqlite_schema` | Generate an in-memory SQLite DB. Assert all 5 tables exist with correct columns. |
| `test_manifest_backward_compat` | Generate manifest, then call `load_hyperparams_manifest_sqlite()` and `load_benchmarks_sqlite()` against it. Assert they succeed without errors. |
| `test_manifest_new_tables` | Assert `audit_rollups` and `descriptions` tables are populated. |
| `test_manifest_empty_benchmarks` | Assert manifest generation succeeds when benchmarks RPC returns empty list. |
| `test_str_or_none_jsonb` | Unit test the `_str_or_none` helper with dict, list, string, None inputs. |

### Integration Tests

| Test | Description |
|---|---|
| `test_full_roundtrip_staging` | Against a staging Supabase instance: create user via auth, publish atom, search catalog, generate manifest. Validates end-to-end. |
| `test_rls_enforced` | Using an anon key, attempt to read `early_access` atoms. Assert 0 results. Using an authenticated key with `effective_tier=general`, assert same. |
| `test_manifest_matches_api` | Compare atoms returned by `/catalog/search` with atoms in the generated manifest.sqlite. Assert consistency. |

### Performance Baseline

Measure P50/P95 latency for catalog search, atom detail, and bounty list endpoints
before and after migration. PostgREST adds HTTP overhead versus a direct asyncpg
connection; verify it stays within acceptable bounds (target: <2x latency increase for
read endpoints).

---

## File Inventory

Files to create or modify, by track:

### Track F
- **Modify**: `sciona/api/deps.py` -- rewrite per F.2
- **Modify**: `sciona/api/routers/auth.py` -- rewrite per F.4
- **Modify**: `sciona/api/models.py` -- add `effective_tier` to `UserResponse`, add `refresh_token` to `TokenResponse`
- **Create**: `tests/api/test_supabase_auth.py`

### Track G
- **Modify**: `sciona/api/app.py` -- lifespan changes per F.5
- **Modify**: `sciona/api/routers/catalog.py` -- rewrite per G.3.1
- **Modify**: `sciona/api/routers/registry.py` -- rewrite per G.3.2
- **Modify**: `sciona/api/routers/bounty.py` -- rewrite per G.3.3
- **Modify**: `sciona/api/routers/verification.py` -- rewrite per G.3.5
- **Modify**: `sciona/api/routers/dashboard.py` -- rewrite per G.3.4 and G.3.6
- **Create**: SQL migration for RPCs listed in G.4
- **Create**: `tests/api/test_supabase_endpoints.py`

### Track H
- **Modify**: `sciona/api/snapshot.py` -- rewrite per H.2
- **Modify**: `sciona/api/routers/catalog.py` -- remove `/catalog/manifest`, add `/catalog/atom/{fqdn}`
- **Create**: `tests/api/test_supabase_snapshot.py`

### Shared
- **Modify**: `pyproject.toml` / `requirements.txt` -- add `supabase>=2.0`
- **Modify**: `.env.example` -- add `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SCIONA_USE_SUPABASE_AUTH`, `SCIONA_USE_SUPABASE_DB`

---

## Dependency Changes

| Package | Action | When |
|---|---|---|
| `supabase>=2.0` | **Add** | Phase 4 start |
| `gotrue` | Transitive via `supabase` | Phase 4 start |
| `asyncpg` | **Remove** | After full cutover (week 5) |
| `PyJWT` | **Remove** | After full cutover (week 5) |

### Environment Variables

| Remove (after cutover) | Add |
|---|---|
| `SCIONA_JWT_PUBLIC_KEY` | `SUPABASE_URL` |
| `SCIONA_JWT_PUBLIC_KEY_PATH` | `SUPABASE_ANON_KEY` |
| `SCIONA_JWT_PRIVATE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` |
| `SCIONA_JWT_PRIVATE_KEY_PATH` | `SCIONA_USE_SUPABASE_AUTH` |
| `SCIONA_MANIFEST_PATH` | `SCIONA_USE_SUPABASE_DB` |
| `GITHUB_OAUTH_CLIENT_ID` (move to Supabase dashboard) | |
| `GITHUB_OAUTH_CLIENT_SECRET` (move to Supabase dashboard) | |

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| supabase-py async client is less mature than asyncpg | Pin to a tested version; keep asyncpg fallback via feature flag |
| PostgREST query builder cannot express all current SQL | Move complex queries to RPCs (already planned for `?` operator and benchmark joins) |
| Supabase `auth.get_user()` adds latency vs local JWT decode | Cache user profile in request state after first call; consider offline JWT verification for hot paths |
| Service role key in application bypasses RLS | Use service role only for manifest generation and admin operations; user-facing requests use the user's JWT |
| SQLite manifest grows with new tables | New tables are small (one row per atom for rollups/descriptions); total size impact is negligible |
| Device flow `sign_in_with_id_token` may not populate `raw_user_meta_data` correctly | The `handle_new_user()` trigger uses COALESCE for all fields; backfill profile in application code as safety net |

---

## Validation Criteria

Phase 4 is complete when all of the following are true:

1. **Auth**
   - [ ] `require_auth` validates Supabase JWTs; custom RS256 keys are not used
   - [ ] `GET /auth/me` returns correct user profile with `effective_tier`
   - [ ] Login flow produces a Supabase session token (not a custom JWT)
   - [ ] Blacklisted users receive 403

2. **Database access**
   - [ ] No imports of `asyncpg` remain in `sciona/api/`
   - [ ] All router endpoints use `get_supabase()` dependency
   - [ ] All raw SQL replaced with PostgREST queries or `supabase.rpc()` calls
   - [ ] RLS is enforced on all read paths (verified by integration tests)

3. **Catalog**
   - [ ] `/catalog/search` reads from `catalog_atoms_index` with FTS support
   - [ ] `/catalog/manifest` endpoint is removed (returns 404)
   - [ ] `/catalog/atom/{fqdn}` returns full document bundle via RPC
   - [ ] `CatalogEntry` response model includes audit/trust fields from the index

4. **Manifest**
   - [ ] `snapshot.py` generates SQLite from Supabase, not asyncpg
   - [ ] `sciona catalog sync` works end-to-end against Supabase
   - [ ] `load_hyperparams_manifest_sqlite()` and `load_benchmarks_sqlite()` work
     unchanged against the generated SQLite

5. **Tests**
   - [ ] All existing tests pass (with updated mocks)
   - [ ] New integration tests pass against Supabase local emulator
   - [ ] Test suite completes in under 1 minute

---

## Rollback Procedure

Phase 4 operates during the dual-read period (old PG is still available as a
read-only fallback). Rollback is straightforward:

1. **Revert the deployment** to the last pre-Phase-4 release. Since `get_db()` and
   the asyncpg pool are still present in the pre-Phase-4 code, the old read path
   activates immediately.

2. **Environment variables**: Restore `SCIONA_JWT_PUBLIC_KEY` and related vars.
   Remove `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` from
   the deployment environment.

3. **CLI clients**: If users have already updated to a CLI that uses Supabase tokens,
   they must `sciona login` again to obtain a legacy JWT. Alternatively, maintain
   backward compatibility by accepting both token formats during the transition
   window:
   ```python
   # Transitional require_auth (temporary, remove after full cutover)
   async def require_auth(request, supabase=Depends(get_supabase)):
       token = extract_bearer_token(request)
       try:
           user = await supabase.auth.get_user(token)
           if user.user:
               return build_profile(user.user)
       except Exception:
           pass
       return await legacy_jwt_auth(token)
   ```

4. **Manifest**: If `sciona catalog sync` was updated to use Supabase, rollback
   means re-enabling the `/catalog/manifest` endpoint. The SQLite schema is
   identical regardless of source, so existing cached manifests remain valid.

5. **Data consistency**: No data migration happens in Phase 4 -- it only changes the
   read/write path. The underlying Supabase data was populated in Phases 2-3 and
   remains intact regardless of which client code is deployed.

### Rollback Triggers

Initiate rollback if any of the following occur within 24 hours of Phase 4 deployment:

- Error rate on any endpoint exceeds 5% (up from baseline <0.1%)
- P95 latency on catalog search exceeds 2 seconds
- Auth failures reported by more than 3 distinct users
- Any data inconsistency between Supabase reads and old PG reads
