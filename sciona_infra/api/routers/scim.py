"""SCIM 2.0 provisioning endpoints for enterprise Authentik users."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter()
_scim_bearer = HTTPBearer(auto_error=False)
SCIM_BEARER_TOKEN = os.environ.get("SCIM_BEARER_TOKEN", "")


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


async def require_scim_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_scim_bearer),
) -> str:
    """Validate the SCIM bearer token."""
    if not SCIM_BEARER_TOKEN:
        raise HTTPException(503, "SCIM provisioning is not configured")
    if credentials is None or credentials.credentials != SCIM_BEARER_TOKEN:
        raise HTTPException(401, "Invalid SCIM bearer token")
    return credentials.credentials


def _get_supabase(request: Request) -> Any:
    client = getattr(request.app.state, "supabase_admin", None)
    if client is None:
        client = getattr(request.app.state, "supabase", None)
    if client is None:
        raise HTTPException(503, "Database not available")
    return client


def _result_data(result: Any) -> Any:
    if result is None:
        return None
    return getattr(result, "data", None)


def _first_row(data: Any) -> dict[str, Any] | None:
    if not data:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_row_to_scim(row: dict[str, Any], base_url: str = "") -> SCIMUser:
    """Convert a database user row to a SCIM User resource."""
    emails: list[SCIMEmail] = []
    if row.get("email"):
        emails = [SCIMEmail(value=str(row["email"]))]

    display_name = str(row.get("display_name", "") or "")
    parts = display_name.split(" ", 1) if display_name else ["", ""]
    given = parts[0]
    family = parts[1] if len(parts) > 1 else ""
    base = base_url.rstrip("/")
    location = f"{base}/scim/v2/Users/{row['user_id']}" if base else f"/scim/v2/Users/{row['user_id']}"

    return SCIMUser(
        id=str(row["user_id"]),
        externalId=str(row.get("scim_external_id", "") or ""),
        userName=str(row.get("email") or row.get("github_login") or row.get("oidc_sub") or ""),
        name=SCIMName(givenName=given, familyName=family, formatted=display_name),
        displayName=display_name,
        emails=emails,
        active=bool(row.get("scim_active", True)),
        meta={
            "resourceType": "User",
            "created": row.get("created_at", ""),
            "lastModified": row.get("updated_at", ""),
            "location": location,
        },
    )


def _extract_tier_from_groups(groups: list[dict[str, str]]) -> str:
    """Map SCIM group display names to platform identity tier."""
    for group in groups:
        display = str(group.get("display", "")).lower()
        if "payee" in display:
            return "payee"
    return "contributor"


def _parse_scim_filter(filter_str: str) -> tuple[str, str] | None:
    """Parse a simple SCIM filter like 'userName eq "alice@example.com"'."""
    match = re.fullmatch(r'(\w+(?:\.\w+)?)\s+eq\s+"([^"]*)"', filter_str.strip())
    if not match:
        return None

    attr_map = {
        "userName": "email",
        "externalId": "scim_external_id",
        "emails.value": "email",
        "displayName": "display_name",
    }
    scim_attr = match.group(1)
    value = match.group(2)
    db_col = attr_map.get(scim_attr)
    if db_col is None:
        return None
    return db_col, value


async def _ensure_auth_user(
    supabase: Any,
    *,
    user_id: str,
    email: str,
    display_name: str,
    org_slug: str,
    auth_provider: str = "oidc",
    oidc_sub: str = "",
) -> None:
    admin = getattr(getattr(supabase, "auth", None), "admin", None)
    if admin is None:
        raise HTTPException(503, "Supabase admin auth is not available")

    user_metadata = {
        "display_name": display_name,
        "org_slug": org_slug,
        "auth_provider": auth_provider,
    }
    if oidc_sub:
        user_metadata["oidc_sub"] = oidc_sub

    app_metadata = {
        "auth_provider": auth_provider,
        "org_slug": org_slug,
    }

    try:
        await admin.get_user_by_id(user_id)
    except Exception:
        await admin.create_user(
            {
                "id": user_id,
                "email": email,
                "email_confirm": True,
                "user_metadata": user_metadata,
                "app_metadata": app_metadata,
            }
        )
    else:
        await admin.update_user_by_id(
            user_id,
            {
                "email": email,
                "email_confirm": True,
                "user_metadata": user_metadata,
                "app_metadata": app_metadata,
            },
        )


@router.post("/scim/v2/Users", status_code=201)
async def create_scim_user(
    user: SCIMUser,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> SCIMUser:
    """Create a new user via SCIM provisioning."""
    supabase = _get_supabase(request)
    if user.externalId:
        existing = (
            await supabase.table("users")
            .select("user_id")
            .eq("scim_external_id", user.externalId)
            .maybe_single()
            .execute()
        )
        if _result_data(existing):
            raise HTTPException(409, "User with this externalId already exists")

    email = ""
    if user.emails:
        primary = next((item for item in user.emails if item.primary), user.emails[0])
        email = str(primary.value).strip()
    if not email:
        email = str(user.userName).strip()
    if not email:
        raise HTTPException(400, "SCIM user requires an email or userName")

    display_name = str(user.displayName or user.name.formatted or user.userName or email)
    tier = _extract_tier_from_groups(user.groups)
    user_id = str(uuid4())
    await _ensure_auth_user(
        supabase,
        user_id=user_id,
        email=email,
        display_name=display_name,
        org_slug="",
        auth_provider="oidc",
        oidc_sub="",
    )

    payload = {
        "user_id": user_id,
        "github_id": 0,
        "github_login": "",
        "display_name": display_name,
        "avatar_url": "",
        "email": email,
        "identity_tier": tier,
        "oidc_sub": None,
        "oidc_issuer": "",
        "org_slug": "",
        "auth_provider": "oidc",
        "scim_external_id": user.externalId or None,
        "scim_active": bool(user.active),
        "updated_at": _current_utc_iso(),
    }
    created = await supabase.table("users").insert(payload).execute()
    row = _first_row(_result_data(created)) or payload
    return _user_row_to_scim(row, base_url=str(request.base_url))


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
    row = _first_row(_result_data(result))
    if row is None:
        raise HTTPException(404, "User not found")
    return _user_row_to_scim(row, base_url=str(request.base_url))


@router.patch("/scim/v2/Users/{user_id}")
async def patch_scim_user(
    user_id: UUID,
    patch: SCIMPatchOp,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> SCIMUser:
    """Apply SCIM PATCH operations to a user."""
    supabase = _get_supabase(request)
    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    row = _first_row(_result_data(result))
    if row is None:
        raise HTTPException(404, "User not found")

    updates: dict[str, Any] = {"updated_at": _current_utc_iso()}
    for op in patch.Operations:
        if str(op.get("op", "")).lower() != "replace":
            continue
        path = str(op.get("path", "") or "")
        value = op.get("value")

        if path == "active":
            updates["scim_active"] = bool(value)
        elif path in {"displayName", "name.formatted"}:
            updates["display_name"] = str(value)
        elif path == "userName":
            updates["email"] = str(value)
        elif path == "emails" and isinstance(value, list) and value:
            primary = next((item for item in value if item.get("primary")), value[0])
            updates["email"] = str(primary.get("value", ""))
        elif not path and isinstance(value, dict):
            if "active" in value:
                updates["scim_active"] = bool(value["active"])
            if "displayName" in value:
                updates["display_name"] = str(value["displayName"])
            if "name" in value and isinstance(value["name"], dict):
                formatted = value["name"].get("formatted")
                if formatted:
                    updates["display_name"] = str(formatted)
            if "emails" in value and isinstance(value["emails"], list) and value["emails"]:
                primary = next((item for item in value["emails"] if item.get("primary")), value["emails"][0])
                updates["email"] = str(primary.get("value", ""))

    await (
        supabase.table("users")
        .update(updates)
        .eq("user_id", str(user_id))
        .execute()
    )
    refreshed = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    updated = _first_row(_result_data(refreshed))
    if updated is None:
        raise HTTPException(500, "Failed to fetch updated user")
    return _user_row_to_scim(updated, base_url=str(request.base_url))


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
async def delete_scim_user(
    user_id: UUID,
    request: Request,
    _token: str = Depends(require_scim_auth),
) -> None:
    """Deactivate a user (soft-delete)."""
    supabase = _get_supabase(request)
    result = (
        await supabase.table("users")
        .select("user_id")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if _first_row(_result_data(result)) is None:
        raise HTTPException(404, "User not found")

    await (
        supabase.table("users")
        .update({"scim_active": False, "is_blacklisted": True, "updated_at": _current_utc_iso()})
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
    """List users with optional SCIM filter support."""
    supabase = _get_supabase(request)
    query = supabase.table("users").select("*", count="exact")
    if filter:
        parsed = _parse_scim_filter(filter)
        if parsed is not None:
            column, value = parsed
            query = query.eq(column, value)

    offset = max(0, startIndex - 1)
    query = query.range(offset, offset + count - 1)

    result = await query.execute()
    rows = _result_data(result) or []
    resources = [_user_row_to_scim(row, base_url=str(request.base_url)) for row in rows]
    total = getattr(result, "count", None)
    if total is None:
        total = len(rows)

    return SCIMListResponse(
        totalResults=int(total),
        startIndex=startIndex,
        itemsPerPage=count,
        Resources=resources,
    )
