"""Authentication endpoints for the platform API."""

from __future__ import annotations

import logging
import os
import time
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from sciona_infra.api.deps import UserProfile, require_auth
from sciona_infra.api.models import (
    DeviceFlowResponse,
    PendingResponse,
    TokenResponse,
    UserResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

GITHUB_DEVICE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "")
AUTHENTIK_CLIENT_ID = os.environ.get("AUTHENTIK_CLIENT_ID", "")
AUTHENTIK_CLIENT_SECRET = os.environ.get("AUTHENTIK_CLIENT_SECRET", "")
ENTERPRISE_CALLBACK_URL = os.environ.get(
    "ENTERPRISE_CALLBACK_URL",
    "http://localhost:8000/auth/enterprise/callback",
)
AUTHENTIK_OIDC_SLUG = os.environ.get("AUTHENTIK_OIDC_SLUG", "sciona-platform")
_oidc_state_store: dict[str, dict[str, str]] = {}


def _request_supabase(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    if state is None:
        return None
    return getattr(state, "supabase", None) or getattr(state, "supabase_admin", None)


def _request_supabase_admin(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    if state is None:
        return None
    return getattr(state, "supabase_admin", None) or getattr(state, "supabase", None)


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _result_data(result: Any) -> Any:
    if result is None:
        return None
    return _attr_or_key(result, "data")


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


def _enterprise_configured() -> bool:
    return bool(AUTHENTIK_URL and AUTHENTIK_CLIENT_ID and AUTHENTIK_CLIENT_SECRET)


def _authentik_oidc_endpoints(base_url: str) -> dict[str, str]:
    """Derive standard OIDC endpoints from the Authentik base URL."""
    prefix = f"{base_url.rstrip('/')}/application/o/{AUTHENTIK_OIDC_SLUG}"
    return {
        "authorization": f"{prefix}/authorize/",
        "token": f"{prefix}/token/",
        "userinfo": f"{prefix}/userinfo/",
    }


async def _upsert_enterprise_user(
    supabase: Any,
    *,
    user_id: str,
    oidc_sub: str,
    oidc_issuer: str,
    org_slug: str,
    email: str,
    display_name: str,
    avatar_url: str,
    identity_tier: str,
) -> dict[str, Any]:
    """Create or update the auth + profile records for an enterprise user."""
    admin = getattr(getattr(supabase, "auth", None), "admin", None)
    if admin is None:
        raise HTTPException(503, "Supabase admin auth is not available")

    user_metadata = {
        "display_name": display_name,
        "org_slug": org_slug,
        "oidc_sub": oidc_sub,
        "auth_provider": "oidc",
    }
    app_metadata = {
        "auth_provider": "oidc",
        "oidc_issuer": oidc_issuer,
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

    payload = {
        "user_id": user_id,
        "github_id": 0,
        "github_login": "",
        "display_name": display_name,
        "avatar_url": avatar_url,
        "email": email,
        "identity_tier": identity_tier,
        "oidc_sub": oidc_sub,
        "oidc_issuer": oidc_issuer,
        "org_slug": org_slug,
        "auth_provider": "oidc",
        "scim_active": True,
    }
    existing = (
        await supabase.table("users")
        .select("*")
        .eq("oidc_sub", oidc_sub)
        .maybe_single()
        .execute()
    )
    existing_data = _result_data(existing)
    if existing_data:
        await (
            supabase.table("users")
            .update(
                {
                    "display_name": display_name,
                    "avatar_url": avatar_url,
                    "email": email,
                    "identity_tier": identity_tier,
                    "oidc_issuer": oidc_issuer,
                    "org_slug": org_slug,
                    "auth_provider": "oidc",
                    "scim_active": True,
                    "updated_at": "now()",
                }
            )
            .eq("oidc_sub", oidc_sub)
            .execute()
        )
        row = dict(existing_data)
        row.update(payload)
        return row

    result = await supabase.table("users").insert(payload).execute()
    created = _result_data(result)
    if isinstance(created, list):
        created = created[0] if created else None
    if not created:
        created = payload
    return dict(created)


def _token_response_from_session(session_response: Any) -> TokenResponse | None:
    session = _attr_or_key(session_response, "session")
    if session is None:
        data = _attr_or_key(session_response, "data")
        session = _attr_or_key(data, "session")
    if session is None:
        return None

    access_token = _attr_or_key(session, "access_token")
    if not access_token:
        return None

    refresh_token = _attr_or_key(session, "refresh_token", "")
    expires_in = _attr_or_key(session, "expires_in")
    if expires_in is None:
        expires_at = _attr_or_key(session, "expires_at")
        if expires_at is not None:
            expires_in = max(int(float(expires_at) - time.time()), 0)
    if expires_in is None:
        expires_in = 30 * 24 * 3600

    return TokenResponse(
        access_token=str(access_token),
        refresh_token=str(refresh_token or ""),
        expires_in=int(expires_in),
    )


@router.get("/auth/login")
async def login_redirect(request: Request) -> dict[str, str]:
    """Return a Supabase GitHub OAuth URL for browser-based login."""
    _annotate_span(**{"auth.flow": "supabase_oauth", "auth.provider": "github"})
    supabase = _request_supabase(request)
    if supabase is None:
        raise HTTPException(503, "Supabase OAuth is not available")
    redirect_to = os.environ.get(
        "SCIONA_SUPABASE_REDIRECT_URL",
        os.environ.get("SUPABASE_REDIRECT_URL", "http://localhost:5173/auth/callback"),
    )
    try:
        response = await supabase.auth.sign_in_with_oauth(
            {"provider": "github", "options": {"redirect_to": redirect_to}}
        )
    except Exception as exc:
        raise HTTPException(503, "Supabase OAuth is not available") from exc

    url = _attr_or_key(response, "url")
    if not url:
        data = _attr_or_key(response, "data")
        url = _attr_or_key(data, "url")
    if not url:
        raise HTTPException(500, "Supabase OAuth URL not returned")
    return {"url": str(url)}


@router.get("/auth/enterprise/login")
async def enterprise_login(org_slug: str) -> RedirectResponse:
    """Redirect the user to Authentik OIDC authorization for their org."""
    _annotate_span(
        **{"auth.flow": "authentik_oidc", "auth.provider": "authentik", "org.slug": org_slug}
    )
    if not _enterprise_configured():
        raise HTTPException(503, "Enterprise SSO is not configured")
    if not org_slug.strip():
        raise HTTPException(400, "org_slug is required")

    endpoints = _authentik_oidc_endpoints(AUTHENTIK_URL)
    state = secrets.token_urlsafe(32)
    _oidc_state_store[state] = {"org_slug": org_slug}
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": AUTHENTIK_CLIENT_ID,
            "redirect_uri": ENTERPRISE_CALLBACK_URL,
            "scope": "openid email profile sciona_tier",
            "state": state,
        }
    )
    return RedirectResponse(f"{endpoints['authorization']}?{params}")


@router.get("/auth/github/device")
async def github_device_start() -> DeviceFlowResponse:
    """Start the legacy GitHub device flow used by the CLI."""
    _annotate_span(**{"auth.flow": "github_device"})
    try:
        import httpx
    except ImportError:
        raise HTTPException(503, "httpx not installed")

    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(503, "GitHub OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GITHUB_DEVICE_URL,
            data={"client_id": client_id, "scope": "read:user user:email"},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    return DeviceFlowResponse(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        expires_in=data.get("expires_in", 900),
        interval=data.get("interval", 5),
    )


@router.post("/auth/github/device/poll")
async def github_device_poll(
    device_code: str,
    request: Request,
) -> TokenResponse | PendingResponse:
    """Poll the device flow and return a Supabase session token."""
    _annotate_span(
        **{
            "auth.flow": "github_device",
            "auth.device_code_present": bool(device_code),
        }
    )
    try:
        import httpx
    except ImportError:
        raise HTTPException(503, "httpx not installed")

    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        if data["error"] == "authorization_pending":
            return PendingResponse(interval=data.get("interval", 5))
        if data["error"] == "slow_down":
            return PendingResponse(
                status="slow_down", interval=data.get("interval", 10)
            )
        raise HTTPException(400, f"GitHub OAuth error: {data['error']}")

    github_token = data["access_token"]

    try:
        import httpx as httpx_mod
    except ImportError:
        raise HTTPException(503, "httpx not installed")

    async with httpx_mod.AsyncClient() as client:
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/json",
            },
        )
        user_resp.raise_for_status()
        user_resp.json()

    supabase = _request_supabase(request)
    if supabase is None:
        raise HTTPException(503, "Supabase Auth is not available")

    try:
        session_response = await supabase.auth.sign_in_with_id_token(
            {"provider": "github", "token": github_token}
        )
    except Exception as exc:
        raise HTTPException(503, "Supabase device login failed") from exc

    token_response = _token_response_from_session(session_response)
    if token_response is None:
        raise HTTPException(503, "Supabase session was not returned")
    return token_response


@router.get("/auth/enterprise/callback")
async def enterprise_callback(
    code: str,
    state: str,
    request: Request,
) -> TokenResponse:
    """Handle the Authentik OIDC callback and upsert the platform user."""
    import httpx

    state_data = _oidc_state_store.pop(state, None)
    if state_data is None:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if not _enterprise_configured():
        raise HTTPException(503, "Enterprise SSO is not configured")

    endpoints = _authentik_oidc_endpoints(AUTHENTIK_URL)
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

    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            endpoints["userinfo"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if userinfo_resp.status_code != 200:
        raise HTTPException(502, "Failed to fetch user info from Authentik")

    userinfo = userinfo_resp.json()
    oidc_sub = str(userinfo.get("sub", "")).strip()
    if not oidc_sub:
        raise HTTPException(502, "OIDC subject identifier missing")

    email = str(userinfo.get("email", "") or userinfo.get("preferred_username", "")).strip()
    if not email:
        raise HTTPException(502, "OIDC email missing")

    display_name = str(userinfo.get("name") or userinfo.get("preferred_username") or email).strip()
    avatar_url = str(userinfo.get("picture", "") or "")
    identity_tier = str(userinfo.get("sciona_tier") or "contributor")
    if identity_tier not in {"contributor", "payee"}:
        identity_tier = "contributor"

    supabase = _request_supabase_admin(request)
    if supabase is None:
        raise HTTPException(503, "Database not available")

    existing = (
        await supabase.table("users")
        .select("*")
        .eq("oidc_sub", oidc_sub)
        .maybe_single()
        .execute()
    )
    existing_data = _result_data(existing)
    if existing_data:
        user_id = str(existing_data.get("user_id"))
    else:
        user_id = str(uuid4())

    row = await _upsert_enterprise_user(
        supabase,
        user_id=user_id,
        oidc_sub=oidc_sub,
        oidc_issuer=AUTHENTIK_URL.rstrip("/"),
        org_slug=str(state_data.get("org_slug", "")),
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        identity_tier=identity_tier,
    )

    _annotate_span(
        **{
            "auth.flow": "authentik_oidc",
            "auth.provider": "authentik",
            "user.id": str(row.get("user_id", "")),
            "user.identity_tier": str(row.get("identity_tier", "")),
        }
    )

    expires_in = token_data.get("expires_in", 3600)
    refresh_token = token_data.get("refresh_token", "")
    return TokenResponse(
        access_token=str(access_token),
        refresh_token=str(refresh_token or ""),
        expires_in=int(expires_in),
    )


@router.get("/auth/me")
async def get_me(user: UserProfile = Depends(require_auth)) -> UserResponse:
    """Return the current authenticated user."""
    _annotate_span(**{"auth.flow": "me", "user.id": str(user.user_id)})
    return UserResponse(
        user_id=UUID(str(user.user_id)),
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        identity_tier=user.identity_tier,
        effective_tier=user.effective_tier,
        reputation_score=user.reputation_score,
        created_at=user.created_at,
    )
