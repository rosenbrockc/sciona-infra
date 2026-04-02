"""Dependency injection for the platform API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer()
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "")
AUTHENTIK_OIDC_SLUG = os.environ.get("AUTHENTIK_OIDC_SLUG", "sciona-platform")


class UserProfile(BaseModel):
    """Minimal public.users profile representation."""

    user_id: str
    github_id: int = 0
    github_login: str = ""
    display_name: str = ""
    avatar_url: str = ""
    email: str = ""
    identity_tier: str = "contributor"
    effective_tier: str = "general"
    reputation_score: int = 0
    is_blacklisted: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


UserRow = UserProfile


def _current_span():
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    try:
        return trace.get_current_span()
    except Exception:
        return None


def _annotate_span(**attributes: Any) -> None:
    span = _current_span()
    if span is None:
        return
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            logger.debug("Failed to set span attribute %s", key, exc_info=True)


async def get_supabase(request: Request) -> Any:
    """Return the configured Supabase client from app state."""
    client = getattr(request.app.state, "supabase_admin", None)
    if client is None:
        client = getattr(request.app.state, "supabase", None)
    if client is None:
        raise HTTPException(503, "Supabase client not available")
    return client


async def get_temporal(request: Request) -> Any | None:
    """Return the configured Temporal client, if one is available."""
    return getattr(request.app.state, "temporal", None)


async def require_temporal(request: Request) -> Any:
    """Return the Temporal client or raise 503 if unavailable."""
    client = getattr(request.app.state, "temporal", None)
    if client is None:
        raise HTTPException(503, "Temporal workflow engine not available")
    return client


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> UserProfile:
    """Validate the configured auth token and return the user profile."""
    try:
        return await _require_auth_supabase(request, credentials)
    except HTTPException as exc:
        if exc.status_code == 403 or not AUTHENTIK_URL:
            raise
        return await _require_auth_oidc(request, credentials, AUTHENTIK_URL)


async def _require_auth_supabase(
    request: Request,
    credentials: HTTPAuthorizationCredentials,
) -> UserProfile:
    """Validate a Supabase JWT and return the caller's public profile."""
    supabase = await get_supabase(request)
    token = credentials.credentials
    try:
        user_response = await supabase.auth.get_user(token)
    except Exception as exc:  # pragma: no cover - exact SDK exceptions vary
        raise HTTPException(401, "Invalid or expired token") from exc

    auth_user = getattr(user_response, "user", None)
    if auth_user is None:
        raise HTTPException(401, "Invalid token")

    result = (
        await supabase.table("users")
        .select("*")
        .eq("user_id", str(auth_user.id))
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

    _annotate_span(
        **{
            "auth.provider": "supabase",
            "user.id": str(data.get("user_id", "")),
            "user.identity_tier": str(data.get("identity_tier", "")),
            "user.effective_tier": str(data.get("effective_tier", "")),
            "user.blacklisted": bool(data.get("is_blacklisted", False)),
        }
    )

    return UserProfile(**data)


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
    userinfo_url = f"{authentik_url.rstrip('/')}/application/o/{AUTHENTIK_OIDC_SLUG}/userinfo/"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(401, "Invalid or expired token")

    userinfo = resp.json()
    oidc_sub = str(userinfo.get("sub", "")).strip()
    if not oidc_sub:
        raise HTTPException(401, "Invalid token: no subject")

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

    _annotate_span(
        **{
            "auth.provider": "authentik",
            "user.id": str(data.get("user_id", "")),
            "user.identity_tier": str(data.get("identity_tier", "")),
            "user.effective_tier": str(data.get("effective_tier", "")),
            "user.blacklisted": bool(data.get("is_blacklisted", False)),
        }
    )

    return UserProfile(**data)
