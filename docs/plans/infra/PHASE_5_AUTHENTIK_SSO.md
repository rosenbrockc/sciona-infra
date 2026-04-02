# Phase 5 — Authentik Enterprise Auth (SSO + SCIM)

**Goal:** Add OIDC-based enterprise SSO login via Authentik and SCIM 2.0 user
provisioning, while preserving the existing Supabase GitHub OAuth flow for
individual users.

**Depends on:** Phase 1 (Authentik running behind proxy).
**Independent of:** Phases 2-4 (OTel, Temporal, OPA).

---

## Table of Contents

1. [Schema migration](#1-schema-migration)
2. [Authentik admin configuration](#2-authentik-admin-configuration)
3. [Backend: enterprise login endpoints](#3-backend-enterprise-login-endpoints)
4. [Backend: SCIM 2.0 provisioning router](#4-backend-scim-20-provisioning-router)
5. [Backend: mount SCIM router in app.py](#5-backend-mount-scim-router)
6. [Frontend: Login page](#6-frontend-login-page)
7. [Frontend: Layout changes](#7-frontend-layout-changes)
8. [Tests](#8-tests)
9. [Environment variables summary](#9-environment-variables-summary)
10. [File summary](#10-file-summary)

---

## 1. Schema migration

The existing `users` table requires `github_id BIGINT UNIQUE NOT NULL`, which
blocks enterprise users who authenticate via OIDC without a GitHub account.
Add columns for OIDC identity and relax the GitHub constraint.

**File:** `supabase/migrations/20260402000000_enterprise_auth.sql` (create)

```sql
-- Allow users without GitHub accounts (enterprise SSO users).
ALTER TABLE users ALTER COLUMN github_id DROP NOT NULL;
ALTER TABLE users ALTER COLUMN github_id SET DEFAULT 0;
DROP INDEX IF EXISTS idx_users_github_id;
-- Keep uniqueness only for non-zero github_id values.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github_id_unique
    ON users (github_id) WHERE github_id IS NOT NULL AND github_id != 0;

-- Enterprise identity columns.
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_sub        TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_issuer     TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS org_slug        TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider   TEXT NOT NULL DEFAULT 'github'
    CHECK (auth_provider IN ('github', 'oidc'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS scim_external_id TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS scim_active     BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_users_org_slug ON users (org_slug);
CREATE INDEX IF NOT EXISTS idx_users_oidc_sub ON users (oidc_sub);
```

---

## 2. Authentik admin configuration

These are **manual steps** performed in the Authentik admin UI
(`https://auth.<domain>/if/admin/`). They are not code changes.

### 2a. Create an OAuth2/OIDC provider

1. Navigate to **Applications > Providers > Create**.
2. Select **OAuth2/OpenID Connect**.
3. Configure:
   - **Name:** `sciona-platform`
   - **Authorization flow:** `default-provider-authorization-implicit-consent`
     (or `explicit-consent` if you want users to approve scopes)
   - **Client type:** Confidential
   - **Client ID:** (auto-generated, save as `AUTHENTIK_CLIENT_ID`)
   - **Client Secret:** (auto-generated, save as `AUTHENTIK_CLIENT_SECRET`)
   - **Redirect URIs:**
     ```
     http://localhost:8000/auth/enterprise/callback
     https://api.<domain>/auth/enterprise/callback
     ```
   - **Signing Key:** Select the auto-generated self-signed certificate
   - **Scopes:** `openid`, `email`, `profile`
   - **Subject mode:** Based on user's username
4. Save. Note the **OpenID Configuration URL:**
   `https://auth.<domain>/application/o/sciona-platform/.well-known/openid-configuration`

### 2b. Create an application

1. Navigate to **Applications > Applications > Create**.
2. Configure:
   - **Name:** `Sciona Platform`
   - **Slug:** `sciona-platform`
   - **Provider:** Select the `sciona-platform` provider from step 2a
   - **Launch URL:** `https://app.<domain>/`
3. Save.

### 2c. Configure group-to-tier mapping

1. Navigate to **Directory > Groups**.
2. Create groups that map to platform tiers:
   - `sciona-contributor` (default group for new enterprise users)
   - `sciona-payee` (users who have completed KYC/Stripe onboarding)
3. For each group, add a **custom attribute:**
   ```json
   { "sciona_tier": "contributor" }
   ```
   or `"payee"` respectively.
4. In the OIDC provider settings, under **Scope mapping**, add a custom scope
   that emits the group's `sciona_tier` attribute into the ID token claims:
   - Create a **Property Mapping** (type: Scope Mapping):
     - **Name:** `sciona-tier`
     - **Scope name:** `sciona_tier`
     - **Expression:**
       ```python
       for group in request.user.ak_groups.all():
           attrs = group.attributes
           if "sciona_tier" in attrs:
               return {"sciona_tier": attrs["sciona_tier"]}
       return {"sciona_tier": "contributor"}
       ```
   - Assign this mapping to the `sciona-platform` provider.

### 2d. Configure SCIM provisioning (Authentik side)

1. Navigate to **Applications > Providers > Create**.
2. Select **SCIM Provider**.
3. Configure:
   - **Name:** `sciona-scim`
   - **URL:** `https://api.<domain>/scim/v2`
   - **Token:** Generate a long random token. Save as `SCIM_BEARER_TOKEN` env var.
   - **Filtering:** Map to the same groups from step 2c.
4. Save and assign to the `Sciona Platform` application.

---

## 3. Backend: enterprise login endpoints

### 3a. Add environment variables

| Variable | Example | Required |
|---|---|---|
| `AUTHENTIK_URL` | `https://auth.example.com` | Yes (for enterprise SSO) |
| `AUTHENTIK_CLIENT_ID` | `aBcDeFgH...` | Yes |
| `AUTHENTIK_CLIENT_SECRET` | `secret...` | Yes |
| `ENTERPRISE_CALLBACK_URL` | `http://localhost:8000/auth/enterprise/callback` | Yes |

### 3b. Modify `sciona/api/routers/auth.py`

Add the following **below** the existing endpoints. All existing code is
preserved unchanged.

**Exact code to append after the `get_me` endpoint (after line 209):**

```python
# ---------------------------------------------------------------------------
# Enterprise SSO via Authentik OIDC
# ---------------------------------------------------------------------------

import secrets
import urllib.parse

from fastapi.responses import RedirectResponse

# In-memory state store for OIDC CSRF tokens.
# Production: use Redis or a DB table with TTL.
_oidc_state_store: dict[str, dict[str, str]] = {}

AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "")
AUTHENTIK_CLIENT_ID = os.environ.get("AUTHENTIK_CLIENT_ID", "")
AUTHENTIK_CLIENT_SECRET = os.environ.get("AUTHENTIK_CLIENT_SECRET", "")
ENTERPRISE_CALLBACK_URL = os.environ.get(
    "ENTERPRISE_CALLBACK_URL",
    "http://localhost:8000/auth/enterprise/callback",
)


def _authentik_oidc_endpoints(base_url: str) -> dict[str, str]:
    """Derive standard OIDC endpoints from the Authentik base URL."""
    slug = "sciona-platform"
    prefix = f"{base_url}/application/o/{slug}"
    return {
        "authorization": f"{prefix}/authorize/",
        "token": f"{prefix}/token/",
        "userinfo": f"{prefix}/userinfo/",
    }


@router.get("/auth/enterprise/login")
async def enterprise_login(org_slug: str) -> RedirectResponse:
    """Redirect the user to Authentik OIDC authorization for their org.

    Query params:
        org_slug: The organization identifier (used for audit/routing).
    """
    if not AUTHENTIK_URL or not AUTHENTIK_CLIENT_ID:
        raise HTTPException(503, "Enterprise SSO is not configured")

    endpoints = _authentik_oidc_endpoints(AUTHENTIK_URL)
    state = secrets.token_urlsafe(32)
    _oidc_state_store[state] = {"org_slug": org_slug}

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": AUTHENTIK_CLIENT_ID,
        "redirect_uri": ENTERPRISE_CALLBACK_URL,
        "scope": "openid email profile sciona_tier",
        "state": state,
    })
    return RedirectResponse(f"{endpoints['authorization']}?{params}")


@router.get("/auth/enterprise/callback")
async def enterprise_callback(
    code: str,
    state: str,
    request: Request,
) -> TokenResponse:
    """Handle the Authentik OIDC callback.

    Exchanges the authorization code for tokens, extracts user info,
    creates or updates the user row, and returns a platform JWT.
    """
    import httpx

    # Validate state
    state_data = _oidc_state_store.pop(state, None)
    if state_data is None:
        raise HTTPException(400, "Invalid or expired OAuth state")
    org_slug = state_data.get("org_slug", "")

    if not AUTHENTIK_URL or not AUTHENTIK_CLIENT_ID:
        raise HTTPException(503, "Enterprise SSO is not configured")

    endpoints = _authentik_oidc_endpoints(AUTHENTIK_URL)

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            endpoints["token"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ENTERPRISE_CALLBACK_URL,
                "client_id": AUTHENTIK_CLIENT_ID,
                "client_secret": AUTHENTIK_CLIENT_SECRET,
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, "Token exchange with Authentik failed")
        token_data = token_resp.json()

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(502, "No access token in Authentik response")

    # Fetch user info
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            endpoints["userinfo"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(502, "Failed to fetch user info from Authentik")
        userinfo = userinfo_resp.json()

    oidc_sub = userinfo.get("sub", "")
    email = userinfo.get("email", "")
    display_name = userinfo.get("name") or userinfo.get("preferred_username", "")
    avatar_url = userinfo.get("picture", "")
    sciona_tier = userinfo.get("sciona_tier", "contributor")

    if not oidc_sub:
        raise HTTPException(502, "OIDC subject identifier missing")

    # Upsert user in database
    supabase = _request_supabase(request)
    if supabase is None:
        raise HTTPException(503, "Database not available")

    # Check if user exists by oidc_sub
    existing = (
        await supabase.table("users")
        .select("user_id")
        .eq("oidc_sub", oidc_sub)
        .maybe_single()
        .execute()
    )
    existing_data = getattr(existing, "data", None)

    if existing_data:
        # Update existing user
        user_id = existing_data["user_id"]
        await (
            supabase.table("users")
            .update({
                "display_name": display_name,
                "avatar_url": avatar_url,
                "email": email,
                "identity_tier": sciona_tier,
                "org_slug": org_slug,
                "updated_at": "now()",
            })
            .eq("user_id", user_id)
            .execute()
        )
    else:
        # Create new user
        result = await (
            supabase.table("users")
            .insert({
                "oidc_sub": oidc_sub,
                "oidc_issuer": AUTHENTIK_URL,
                "auth_provider": "oidc",
                "org_slug": org_slug,
                "github_id": 0,
                "github_login": "",
                "display_name": display_name,
                "avatar_url": avatar_url,
                "email": email,
                "identity_tier": sciona_tier,
            })
            .execute()
        )
        result_data = getattr(result, "data", None)
        if not result_data:
            raise HTTPException(500, "Failed to create enterprise user")
        user_id = result_data[0]["user_id"] if isinstance(result_data, list) else result_data["user_id"]

    # Mint a platform token via Supabase admin
    # Use the Authentik tokens as-is for the session, wrapped in our TokenResponse
    expires_in = token_data.get("expires_in", 3600)
    return TokenResponse(
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        expires_in=int(expires_in),
    )
```

### 3c. Update `sciona/api/deps.py`

The `require_auth` function currently only validates Supabase JWTs. Enterprise
users receive Authentik JWTs. Add a fallback path that validates Authentik
tokens via the userinfo endpoint.

**Exact edit — replace the `require_auth` function (lines 46-51):**

```python
async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> UserProfile:
    """Validate the configured auth token and return the user profile.

    Tries Supabase JWT validation first. If that fails and Authentik is
    configured, falls back to Authentik token introspection.
    """
    try:
        return await _require_auth_supabase(request, credentials)
    except HTTPException:
        # If Authentik is configured, try OIDC token validation
        authentik_url = os.environ.get("AUTHENTIK_URL", "")
        if authentik_url:
            return await _require_auth_oidc(request, credentials, authentik_url)
        raise
```

**Exact code to append at the end of `deps.py`:**

```python
import os


async def _require_auth_oidc(
    request: Request,
    credentials: HTTPAuthorizationCredentials,
    authentik_url: str,
) -> UserProfile:
    """Validate an Authentik OIDC access token via the userinfo endpoint."""
    try:
        import httpx
    except ImportError:
        raise HTTPException(503, "httpx not installed")

    token = credentials.credentials
    userinfo_url = f"{authentik_url}/application/o/sciona-platform/userinfo/"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(401, "Invalid or expired token")

    userinfo = resp.json()
    oidc_sub = userinfo.get("sub", "")
    if not oidc_sub:
        raise HTTPException(401, "Invalid token: no subject")

    # Look up user by oidc_sub
    supabase = await get_supabase(request)
    result = (
        await supabase.table("users")
        .select("*")
        .eq("oidc_sub", oidc_sub)
        .maybe_single()
        .execute()
    )
    data = getattr(result, "data", None)
    if not data:
        raise HTTPException(401, "User profile not found")
    if data.get("is_blacklisted"):
        raise HTTPException(403, "Account suspended")
    if not data.get("scim_active", True):
        raise HTTPException(403, "Account deactivated by organization")

    return UserProfile(**data)
```

Note: the `import os` should be placed at the top of the file alongside the
existing imports. If `os` is not yet imported there, add it to the import block.

---

## 4. Backend: SCIM 2.0 provisioning router

**File:** `sciona/api/routers/scim.py` (create)

This implements the SCIM 2.0 core schema for Users, sufficient for Authentik's
SCIM provider to push user lifecycle events.

```python
"""SCIM 2.0 provisioning endpoints.

Authentik pushes user create/update/deactivate events here.
All endpoints require a bearer token matching SCIM_BEARER_TOKEN.

Reference: RFC 7644 (SCIM Protocol), RFC 7643 (SCIM Core Schema).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter()
_scim_bearer = HTTPBearer()

SCIM_BEARER_TOKEN = os.environ.get("SCIM_BEARER_TOKEN", "")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def require_scim_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_scim_bearer),
) -> str:
    """Validate the SCIM bearer token."""
    if not SCIM_BEARER_TOKEN:
        raise HTTPException(503, "SCIM provisioning is not configured")
    if credentials.credentials != SCIM_BEARER_TOKEN:
        raise HTTPException(401, "Invalid SCIM bearer token")
    return credentials.credentials


# ---------------------------------------------------------------------------
# Models (SCIM 2.0 wire format)
# ---------------------------------------------------------------------------


class SCIMName(BaseModel):
    givenName: str = ""
    familyName: str = ""
    formatted: str = ""


class SCIMEmail(BaseModel):
    value: str
    type: str = "work"
    primary: bool = True


class SCIMUser(BaseModel):
    """SCIM 2.0 User resource (subset relevant to this platform)."""
    schemas: list[str] = Field(
        default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"]
    )
    id: str = ""
    externalId: str = ""
    userName: str = ""
    name: SCIMName = Field(default_factory=SCIMName)
    displayName: str = ""
    emails: list[SCIMEmail] = Field(default_factory=list)
    active: bool = True
    groups: list[dict[str, str]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class SCIMListResponse(BaseModel):
    schemas: list[str] = Field(
        default_factory=lambda: ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    )
    totalResults: int = 0
    startIndex: int = 1
    itemsPerPage: int = 50
    Resources: list[SCIMUser] = Field(default_factory=list)


class SCIMPatchOp(BaseModel):
    schemas: list[str] = Field(
        default_factory=lambda: ["urn:ietf:params:scim:api:messages:2.0:PatchOp"]
    )
    Operations: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_supabase(request: Request) -> Any:
    client = getattr(request.app.state, "supabase_admin", None)
    if client is None:
        client = getattr(request.app.state, "supabase", None)
    if client is None:
        raise HTTPException(503, "Database not available")
    return client


def _user_row_to_scim(row: dict, base_url: str = "") -> SCIMUser:
    """Convert a database user row to a SCIM User resource."""
    emails = []
    if row.get("email"):
        emails = [SCIMEmail(value=row["email"])]

    display_name = row.get("display_name", "")
    parts = display_name.split(" ", 1) if display_name else ["", ""]
    given = parts[0]
    family = parts[1] if len(parts) > 1 else ""

    return SCIMUser(
        id=str(row["user_id"]),
        externalId=row.get("scim_external_id", "") or "",
        userName=row.get("email") or row.get("github_login", ""),
        name=SCIMName(givenName=given, familyName=family, formatted=display_name),
        displayName=display_name,
        emails=emails,
        active=row.get("scim_active", True),
        meta={
            "resourceType": "User",
            "created": row.get("created_at", ""),
            "lastModified": row.get("updated_at", ""),
            "location": f"{base_url}/scim/v2/Users/{row['user_id']}",
        },
    )


def _extract_tier_from_groups(groups: list[dict[str, str]]) -> str:
    """Map SCIM group display names to platform identity tier."""
    for group in groups:
        display = group.get("display", "").lower()
        if "payee" in display:
            return "payee"
    return "contributor"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/scim/v2/Users", status_code=201)
async def create_scim_user(
    user: SCIMUser,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> SCIMUser:
    """Create a new user via SCIM provisioning."""
    supabase = _get_supabase(request)

    email = ""
    if user.emails:
        primary = next((e for e in user.emails if e.primary), user.emails[0])
        email = primary.value

    display_name = user.displayName or user.name.formatted or user.userName
    tier = _extract_tier_from_groups(user.groups)

    # Check for existing user by externalId
    if user.externalId:
        existing = (
            await supabase.table("users")
            .select("user_id")
            .eq("scim_external_id", user.externalId)
            .maybe_single()
            .execute()
        )
        if getattr(existing, "data", None):
            raise HTTPException(409, "User with this externalId already exists")

    result = await (
        supabase.table("users")
        .insert({
            "scim_external_id": user.externalId or None,
            "auth_provider": "oidc",
            "github_id": 0,
            "github_login": "",
            "display_name": display_name,
            "email": email,
            "identity_tier": tier,
            "scim_active": user.active,
            "org_slug": "",
        })
        .execute()
    )
    result_data = getattr(result, "data", None)
    if not result_data:
        raise HTTPException(500, "Failed to create SCIM user")
    row = result_data[0] if isinstance(result_data, list) else result_data

    return _user_row_to_scim(row)


@router.get("/scim/v2/Users/{user_id}")
async def get_scim_user(
    user_id: UUID,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> SCIMUser:
    """Get a single user by ID."""
    supabase = _get_supabase(request)

    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    data = getattr(result, "data", None)
    if not data:
        raise HTTPException(404, "User not found")

    return _user_row_to_scim(data)


@router.patch("/scim/v2/Users/{user_id}")
async def patch_scim_user(
    user_id: UUID,
    patch: SCIMPatchOp,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> SCIMUser:
    """Apply a SCIM PATCH operation to a user.

    Supports the operations Authentik typically sends:
    - Replace active (deactivate/reactivate)
    - Replace name, emails, displayName
    """
    supabase = _get_supabase(request)

    # Fetch current user
    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    data = getattr(result, "data", None)
    if not data:
        raise HTTPException(404, "User not found")

    updates: dict[str, Any] = {"updated_at": "now()"}

    for op in patch.Operations:
        operation = op.get("op", "").lower()
        path = op.get("path", "")
        value = op.get("value")

        if operation == "replace":
            if path == "active":
                updates["scim_active"] = bool(value)
            elif path == "displayName":
                updates["display_name"] = str(value)
            elif path == "name.formatted":
                updates["display_name"] = str(value)
            elif path == "emails":
                if isinstance(value, list) and value:
                    primary = next(
                        (e for e in value if e.get("primary")), value[0]
                    )
                    updates["email"] = primary.get("value", "")
            elif path == "userName":
                # userName maps to email for OIDC users
                updates["email"] = str(value)
            # If no path, value is a dict of attributes to replace
            elif not path and isinstance(value, dict):
                if "active" in value:
                    updates["scim_active"] = bool(value["active"])
                if "displayName" in value:
                    updates["display_name"] = str(value["displayName"])
                if "emails" in value and isinstance(value["emails"], list):
                    emails = value["emails"]
                    if emails:
                        primary = next(
                            (e for e in emails if e.get("primary")), emails[0]
                        )
                        updates["email"] = primary.get("value", "")

    await (
        supabase.table("users")
        .update(updates)
        .eq("user_id", str(user_id))
        .execute()
    )

    # Fetch updated row
    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    data = getattr(result, "data", None)
    if not data:
        raise HTTPException(500, "Failed to fetch updated user")

    return _user_row_to_scim(data)


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
async def delete_scim_user(
    user_id: UUID,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> None:
    """Deactivate a user (soft-delete). SCIM DELETE = deactivate, not purge."""
    supabase = _get_supabase(request)

    result = (
        await supabase.table("users")
        .select("user_id")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not getattr(result, "data", None):
        raise HTTPException(404, "User not found")

    await (
        supabase.table("users")
        .update({"scim_active": False, "is_blacklisted": True, "updated_at": "now()"})
        .eq("user_id", str(user_id))
        .execute()
    )


@router.get("/scim/v2/Users")
async def list_scim_users(
    request: Request,
    startIndex: int = 1,
    count: int = 50,
    filter: str = "",
    _token: str = Depends(require_scim_auth),
) -> SCIMListResponse:
    """List users with optional SCIM filter support.

    Supported filters (subset):
        userName eq "value"
        externalId eq "value"
        emails.value eq "value"
    """
    supabase = _get_supabase(request)

    query = supabase.table("users").select("*", count="exact")

    # Parse simple SCIM filter expressions
    if filter:
        parsed = _parse_scim_filter(filter)
        if parsed:
            col, val = parsed
            query = query.eq(col, val)

    # Pagination: SCIM uses 1-based startIndex
    offset = max(0, startIndex - 1)
    query = query.range(offset, offset + count - 1)

    result = await query.execute()
    rows = getattr(result, "data", []) or []
    total = getattr(result, "count", len(rows)) or len(rows)

    resources = [_user_row_to_scim(row) for row in rows]

    return SCIMListResponse(
        totalResults=total,
        startIndex=startIndex,
        itemsPerPage=count,
        Resources=resources,
    )


def _parse_scim_filter(filter_str: str) -> tuple[str, str] | None:
    """Parse a simple SCIM filter like 'userName eq "alice@example.com"'.

    Returns (db_column, value) or None if unparseable.
    """
    import re

    match = re.match(
        r'(\w+(?:\.\w+)?)\s+eq\s+"([^"]*)"', filter_str.strip()
    )
    if not match:
        return None

    scim_attr = match.group(1)
    value = match.group(2)

    # Map SCIM attribute names to database columns
    attr_map = {
        "userName": "email",
        "externalId": "scim_external_id",
        "emails.value": "email",
        "displayName": "display_name",
    }
    db_col = attr_map.get(scim_attr)
    if not db_col:
        return None

    return (db_col, value)
```

---

## 5. Backend: mount SCIM router

**File:** `sciona/api/app.py` (modify)

**Exact edit — add after line 101 (`from sciona.api.routers.verification ...`):**

```python
    from sciona.api.routers.scim import router as scim_router
```

**Exact edit — add after line 115 (`application.include_router(dashboard_router ...`):**

```python
    application.include_router(scim_router, tags=["scim"])
```

---

## 6. Frontend: Login page

**File:** `frontend/src/pages/Login.tsx` (create)

```tsx
import { useState, FormEvent } from "react";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export default function Login() {
  const [orgSlug, setOrgSlug] = useState("");
  const [showEnterprise, setShowEnterprise] = useState(false);

  async function handleGitHubLogin() {
    const resp = await fetch(`${API_BASE}/auth/login`);
    const data = await resp.json();
    if (data.url) {
      window.location.href = data.url;
    }
  }

  function handleEnterpriseLogin(e: FormEvent) {
    e.preventDefault();
    if (!orgSlug.trim()) return;
    window.location.href = `${API_BASE}/auth/enterprise/login?org_slug=${encodeURIComponent(orgSlug.trim())}`;
  }

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-accent">Sign In</h1>
          <p className="text-muted text-sm mt-1">
            Algorithmic Commons Platform
          </p>
        </div>

        {/* GitHub OAuth */}
        <button
          onClick={handleGitHubLogin}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-panel border border-border rounded-lg text-sm font-medium hover:bg-panel-soft transition-colors"
        >
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" />
          </svg>
          Sign in with GitHub
        </button>

        {/* Divider */}
        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="bg-surface px-2 text-muted">or</span>
          </div>
        </div>

        {/* Enterprise SSO */}
        {!showEnterprise ? (
          <button
            onClick={() => setShowEnterprise(true)}
            className="w-full px-4 py-3 border border-border rounded-lg text-sm text-muted hover:text-gray-200 hover:bg-panel-soft transition-colors"
          >
            Enterprise SSO
          </button>
        ) : (
          <form onSubmit={handleEnterpriseLogin} className="space-y-3">
            <label className="block">
              <span className="text-xs text-muted">Organization slug</span>
              <input
                type="text"
                value={orgSlug}
                onChange={(e) => setOrgSlug(e.target.value)}
                placeholder="your-company"
                autoFocus
                className="mt-1 w-full px-3 py-2 bg-panel border border-border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
            <button
              type="submit"
              disabled={!orgSlug.trim()}
              className="w-full px-4 py-3 bg-accent text-white rounded-lg text-sm font-medium disabled:opacity-40 hover:opacity-90 transition-opacity"
            >
              Continue with SSO
            </button>
            <button
              type="button"
              onClick={() => setShowEnterprise(false)}
              className="w-full text-xs text-muted hover:text-gray-300"
            >
              Back
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
```

**File:** `frontend/src/App.tsx` (modify)

**Exact edit — add import after line 9 (`import OriginatorProfile ...`):**

```typescript
import Login from "./pages/Login";
```

**Exact edit — add route after line 16 (`<Route index element={<Home />} />`):**

```typescript
        <Route path="login" element={<Login />} />
```

---

## 7. Frontend: Layout changes

**File:** `frontend/src/components/Layout.tsx` (modify)

**Exact edit — replace the footer div (lines 40-42):**

```typescript
        <div className="p-4 border-t border-border space-y-2">
          <a
            href="/login"
            className="block text-xs text-muted hover:text-gray-200 transition-colors"
          >
            Sign in
          </a>
          <span className="block text-xs text-muted">v0.1.0</span>
        </div>
```

---

## 8. Tests

**File:** `tests/test_enterprise_auth.py` (create)

```python
"""Tests for enterprise SSO and SCIM provisioning endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sciona.api.routers import auth as auth_mod
from sciona.api.routers import scim as scim_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSupabaseQuery:
    """Chainable Supabase query mock."""

    def __init__(self, data: Any = None, *, count: int | None = None):
        self._data = data
        self._count = count

    def select(self, *args, **kwargs):
        return self

    def insert(self, _payload):
        return self

    def update(self, _payload):
        return self

    def eq(self, _field: str, _value):
        return self

    def maybe_single(self):
        return self

    def range(self, _start: int, _end: int):
        return self

    async def execute(self):
        ns = SimpleNamespace(data=self._data)
        if self._count is not None:
            ns.count = self._count
        return ns


class _FakeSupabase:
    def __init__(self, *, rows: dict | list | None = None, count: int | None = None):
        self._rows = rows
        self._count = count
        self.auth = SimpleNamespace(get_user=self._get_user)

    async def _get_user(self, _token: str):
        return SimpleNamespace(user=None)

    def table(self, _name: str):
        return _FakeSupabaseQuery(self._rows, count=self._count)


def _make_request(supabase: _FakeSupabase | None = None) -> SimpleNamespace:
    state = SimpleNamespace()
    if supabase is not None:
        state.supabase = supabase
        state.supabase_admin = supabase
    return SimpleNamespace(app=SimpleNamespace(state=state))


# ---------------------------------------------------------------------------
# Enterprise login
# ---------------------------------------------------------------------------


class TestEnterpriseLogin:
    def test_login_redirect_requires_config(self, monkeypatch):
        """enterprise_login raises 503 when AUTHENTIK_URL is not set."""
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "")

        with pytest.raises(HTTPException) as exc:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                auth_mod.enterprise_login(org_slug="acme")
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_login_redirect_builds_correct_url(self, monkeypatch):
        """enterprise_login returns a redirect to Authentik."""
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(
            auth_mod, "ENTERPRISE_CALLBACK_URL",
            "http://localhost:8000/auth/enterprise/callback",
        )

        response = await auth_mod.enterprise_login(org_slug="acme")

        assert response.status_code == 307
        location = response.headers["location"]
        assert "auth.test.com" in location
        assert "client_id=test-client-id" in location
        assert "sciona-platform" in location

    @pytest.mark.asyncio
    async def test_callback_rejects_invalid_state(self, monkeypatch):
        """enterprise_callback rejects unknown state parameter."""
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "test-client-id")

        request = _make_request(_FakeSupabase())
        with pytest.raises(HTTPException) as exc:
            await auth_mod.enterprise_callback(
                code="test-code", state="bogus-state", request=request
            )
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# SCIM auth
# ---------------------------------------------------------------------------


class TestSCIMAuth:
    @pytest.mark.asyncio
    async def test_scim_auth_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")
        credentials = SimpleNamespace(credentials="wrong-token")

        with pytest.raises(HTTPException) as exc:
            await scim_mod.require_scim_auth(credentials)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_scim_auth_accepts_correct_token(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")
        credentials = SimpleNamespace(credentials="correct-token")

        result = await scim_mod.require_scim_auth(credentials)
        assert result == "correct-token"

    @pytest.mark.asyncio
    async def test_scim_auth_503_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "")
        credentials = SimpleNamespace(credentials="any")

        with pytest.raises(HTTPException) as exc:
            await scim_mod.require_scim_auth(credentials)
        assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# SCIM filter parsing
# ---------------------------------------------------------------------------


class TestSCIMFilterParsing:
    def test_parse_username_eq(self):
        result = scim_mod._parse_scim_filter('userName eq "alice@example.com"')
        assert result == ("email", "alice@example.com")

    def test_parse_external_id_eq(self):
        result = scim_mod._parse_scim_filter('externalId eq "ext-123"')
        assert result == ("scim_external_id", "ext-123")

    def test_parse_unknown_attr_returns_none(self):
        result = scim_mod._parse_scim_filter('unknownAttr eq "value"')
        assert result is None

    def test_parse_malformed_returns_none(self):
        result = scim_mod._parse_scim_filter("not a valid filter")
        assert result is None


# ---------------------------------------------------------------------------
# SCIM user row conversion
# ---------------------------------------------------------------------------


class TestSCIMUserConversion:
    def test_user_row_to_scim(self):
        row = {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Alice Smith",
            "email": "alice@example.com",
            "scim_external_id": "ext-1",
            "scim_active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        scim_user = scim_mod._user_row_to_scim(row)
        assert scim_user.id == "11111111-1111-1111-1111-111111111111"
        assert scim_user.displayName == "Alice Smith"
        assert scim_user.name.givenName == "Alice"
        assert scim_user.name.familyName == "Smith"
        assert scim_user.emails[0].value == "alice@example.com"
        assert scim_user.active is True

    def test_user_row_to_scim_no_email(self):
        row = {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "display_name": "Bob",
            "email": "",
            "github_login": "bob",
            "scim_active": True,
            "created_at": "",
            "updated_at": "",
        }
        scim_user = scim_mod._user_row_to_scim(row)
        assert scim_user.emails == []
        assert scim_user.userName == "bob"


# ---------------------------------------------------------------------------
# SCIM group-to-tier mapping
# ---------------------------------------------------------------------------


class TestSCIMTierMapping:
    def test_payee_group(self):
        groups = [{"display": "sciona-payee", "value": "group-1"}]
        assert scim_mod._extract_tier_from_groups(groups) == "payee"

    def test_default_tier(self):
        groups = [{"display": "sciona-contributor", "value": "group-2"}]
        assert scim_mod._extract_tier_from_groups(groups) == "contributor"

    def test_empty_groups(self):
        assert scim_mod._extract_tier_from_groups([]) == "contributor"


# ---------------------------------------------------------------------------
# SCIM CRUD (with mocked Supabase)
# ---------------------------------------------------------------------------


class TestSCIMCreateUser:
    @pytest.mark.asyncio
    async def test_create_user_success(self):
        user_id = str(uuid4())
        created_row = {
            "user_id": user_id,
            "display_name": "New User",
            "email": "new@example.com",
            "scim_external_id": "ext-new",
            "scim_active": True,
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
        }
        supabase = _FakeSupabase(rows=[created_row])
        # Override the table method to handle both select (for dup check) and insert
        call_count = {"n": 0}
        original_table = supabase.table

        def _table(name: str):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: duplicate check, return None
                return _FakeSupabaseQuery(None)
            # Second call: insert, return created row
            return _FakeSupabaseQuery([created_row])

        supabase.table = _table
        request = _make_request(supabase)

        scim_user = scim_mod.SCIMUser(
            externalId="ext-new",
            userName="new@example.com",
            displayName="New User",
            emails=[scim_mod.SCIMEmail(value="new@example.com")],
        )

        result = await scim_mod.create_scim_user(
            user=scim_user, request=request, _token="tok"
        )
        assert result.displayName == "New User"


class TestSCIMGetUser:
    @pytest.mark.asyncio
    async def test_get_user_found(self):
        user_id = "11111111-1111-1111-1111-111111111111"
        row = {
            "user_id": user_id,
            "display_name": "Test",
            "email": "test@example.com",
            "scim_active": True,
            "created_at": "",
            "updated_at": "",
        }
        supabase = _FakeSupabase(rows=row)
        request = _make_request(supabase)

        from uuid import UUID
        result = await scim_mod.get_scim_user(
            user_id=UUID(user_id), request=request, _token="tok"
        )
        assert result.displayName == "Test"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self):
        supabase = _FakeSupabase(rows=None)
        request = _make_request(supabase)

        from uuid import UUID
        with pytest.raises(HTTPException) as exc:
            await scim_mod.get_scim_user(
                user_id=UUID("11111111-1111-1111-1111-111111111111"),
                request=request,
                _token="tok",
            )
        assert exc.value.status_code == 404


class TestSCIMDeleteUser:
    @pytest.mark.asyncio
    async def test_delete_deactivates_user(self):
        user_id = "11111111-1111-1111-1111-111111111111"
        supabase = _FakeSupabase(rows={"user_id": user_id})
        request = _make_request(supabase)

        from uuid import UUID
        result = await scim_mod.delete_scim_user(
            user_id=UUID(user_id), request=request, _token="tok"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self):
        supabase = _FakeSupabase(rows=None)
        request = _make_request(supabase)

        from uuid import UUID
        with pytest.raises(HTTPException) as exc:
            await scim_mod.delete_scim_user(
                user_id=UUID("11111111-1111-1111-1111-111111111111"),
                request=request,
                _token="tok",
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# OIDC token validation (deps.py fallback)
# ---------------------------------------------------------------------------


class TestOIDCTokenValidation:
    @pytest.mark.asyncio
    async def test_oidc_fallback_with_valid_token(self, monkeypatch):
        """When Supabase auth fails and Authentik is configured, try OIDC."""
        from sciona.api import deps

        user_row = {
            "user_id": "33333333-3333-3333-3333-333333333333",
            "github_id": 0,
            "github_login": "",
            "display_name": "Enterprise User",
            "avatar_url": "",
            "email": "enterprise@corp.com",
            "identity_tier": "contributor",
            "effective_tier": "general",
            "is_blacklisted": False,
            "scim_active": True,
            "oidc_sub": "oidc-sub-123",
        }

        class _FakeHTTPResponse:
            status_code = 200

            def json(self):
                return {"sub": "oidc-sub-123", "email": "enterprise@corp.com"}

        class _FakeHTTPClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, _url, **_kwargs):
                return _FakeHTTPResponse()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeHTTPClient())

        supabase = _FakeSupabase(rows=user_row)
        request = _make_request(supabase)
        credentials = SimpleNamespace(credentials="oidc-token")

        result = await deps._require_auth_oidc(
            request, credentials, "https://auth.test.com"
        )
        assert result.display_name == "Enterprise User"
        assert result.email == "enterprise@corp.com"

    @pytest.mark.asyncio
    async def test_oidc_fallback_rejects_invalid_token(self, monkeypatch):
        from sciona.api import deps

        class _FakeHTTPResponse:
            status_code = 401

            def json(self):
                return {}

        class _FakeHTTPClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, _url, **_kwargs):
                return _FakeHTTPResponse()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeHTTPClient())

        request = _make_request(_FakeSupabase())
        credentials = SimpleNamespace(credentials="bad-token")

        with pytest.raises(HTTPException) as exc:
            await deps._require_auth_oidc(
                request, credentials, "https://auth.test.com"
            )
        assert exc.value.status_code == 401
```

---

## 9. Environment variables summary

| Variable | Purpose | Where |
|---|---|---|
| `AUTHENTIK_URL` | Base URL of the Authentik server | API server |
| `AUTHENTIK_CLIENT_ID` | OAuth2 client ID from Authentik provider | API server |
| `AUTHENTIK_CLIENT_SECRET` | OAuth2 client secret from Authentik provider | API server |
| `ENTERPRISE_CALLBACK_URL` | Full URL for the OIDC callback endpoint | API server |
| `SCIM_BEARER_TOKEN` | Shared secret for SCIM endpoint auth | API server + Authentik SCIM provider |

Add these to `docker/.env.example`:

```env
# Phase 5: Authentik Enterprise SSO
AUTHENTIK_URL=https://auth.yourdomain.com
AUTHENTIK_CLIENT_ID=
AUTHENTIK_CLIENT_SECRET=
ENTERPRISE_CALLBACK_URL=https://api.yourdomain.com/auth/enterprise/callback
SCIM_BEARER_TOKEN=
```

---

## 10. File summary

| File | Action | Description |
|---|---|---|
| `supabase/migrations/20260402000000_enterprise_auth.sql` | Create | Schema migration for OIDC/SCIM columns |
| `sciona/api/routers/auth.py` | Modify | Add `enterprise_login` + `enterprise_callback` endpoints |
| `sciona/api/deps.py` | Modify | Add OIDC token fallback in `require_auth`, add `_require_auth_oidc` |
| `sciona/api/routers/scim.py` | Create | Full SCIM 2.0 Users endpoint set |
| `sciona/api/app.py` | Modify | Mount SCIM router |
| `frontend/src/pages/Login.tsx` | Create | Login page with GitHub + Enterprise SSO |
| `frontend/src/App.tsx` | Modify | Add `/login` route |
| `frontend/src/components/Layout.tsx` | Modify | Add sign-in link to sidebar footer |
| `tests/test_enterprise_auth.py` | Create | Tests for SSO, SCIM auth, filter parsing, CRUD |
| `docker/.env.example` | Modify | Add SSO/SCIM env vars |

### Execution order within this phase

1. Apply schema migration (section 1)
2. Create `sciona/api/routers/scim.py` (section 4)
3. Modify `sciona/api/routers/auth.py` (section 3)
4. Modify `sciona/api/deps.py` (section 3c)
5. Modify `sciona/api/app.py` (section 5)
6. Create `frontend/src/pages/Login.tsx` (section 6)
7. Modify `frontend/src/App.tsx` and `Layout.tsx` (sections 6-7)
8. Create `tests/test_enterprise_auth.py` (section 8)
9. Configure Authentik admin UI (section 2) -- requires running Authentik instance
