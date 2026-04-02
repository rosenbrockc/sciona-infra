from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sciona_infra.api import deps
from sciona_infra.api.routers import auth as auth_router


class _FakeSupabaseQuery:
    def __init__(self, data):
        self._data = data

    def select(self, _fields: str):
        return self

    def eq(self, _field: str, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(self, *, profile: dict | None = None, session=None, user_id: str = "user-1"):
        self._profile = profile
        self._session = session
        self._user_id = user_id
        self.auth = SimpleNamespace(
            get_user=self._get_user,
            sign_in_with_id_token=self._sign_in_with_id_token,
        )

    async def _get_user(self, _token: str):
        return SimpleNamespace(user=SimpleNamespace(id=self._user_id))

    async def _sign_in_with_id_token(self, _payload: dict):
        return SimpleNamespace(session=self._session)

    def table(self, _name: str):
        return _FakeSupabaseQuery(self._profile)


@pytest.mark.asyncio
async def test_require_auth_supabase_valid_token() -> None:
    profile = {
        "user_id": "user-1",
        "github_id": 1,
        "github_login": "alice",
        "display_name": "Alice",
        "avatar_url": "",
        "email": "alice@example.com",
        "identity_tier": "contributor",
        "effective_tier": "early_access",
        "is_blacklisted": False,
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(supabase=_FakeSupabase(profile=profile))))
    credentials = SimpleNamespace(credentials="token")

    result = await deps.require_auth(request, credentials=credentials)

    assert result.github_login == "alice"
    assert result.effective_tier == "early_access"


@pytest.mark.asyncio
async def test_require_auth_supabase_blacklisted() -> None:
    profile = {
        "user_id": "user-1",
        "github_id": 1,
        "github_login": "alice",
        "display_name": "Alice",
        "avatar_url": "",
        "email": "alice@example.com",
        "identity_tier": "contributor",
        "effective_tier": "general",
        "is_blacklisted": True,
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(supabase=_FakeSupabase(profile=profile))))
    credentials = SimpleNamespace(credentials="token")

    with pytest.raises(HTTPException) as exc:
        await deps.require_auth(request, credentials=credentials)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_device_flow_returns_supabase_session(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    session = SimpleNamespace(
        access_token="supabase-access",
        refresh_token="supabase-refresh",
        expires_in=3600,
    )
    supabase = _FakeSupabase(profile=None, session=session)

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, **_kwargs):
            if "access_token" in url:
                return _Response({"access_token": "github-token"})
            return _Response({"access_token": "github-token"})

        async def get(self, _url: str, **_kwargs):
            return _Response({"id": 123, "login": "alice"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _Client())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(supabase=supabase)))

    result = await auth_router.github_device_poll("device-code", request)

    assert result.access_token == "supabase-access"
    assert result.refresh_token == "supabase-refresh"


@pytest.mark.asyncio
async def test_device_flow_raises_when_supabase_session_missing(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    supabase = _FakeSupabase(profile=None, session=None)

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url: str, **_kwargs):
            return _Response({"access_token": "github-token"})

        async def get(self, _url: str, **_kwargs):
            return _Response({"id": 123, "login": "alice"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _Client())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(supabase=supabase)))

    with pytest.raises(HTTPException) as exc:
        await auth_router.github_device_poll("device-code", request)

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_auth_me_returns_effective_tier() -> None:
    created_at = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
    user = deps.UserProfile(
        user_id="11111111-1111-1111-1111-111111111111",
        github_id=1,
        github_login="alice",
        display_name="Alice",
        avatar_url="",
        email="alice@example.com",
        identity_tier="contributor",
        effective_tier="internal",
        reputation_score=17,
        is_blacklisted=False,
        created_at=created_at,
    )

    result = await auth_router.get_me(user=user)

    assert result.effective_tier == "internal"
    assert result.reputation_score == 17
    assert result.created_at == created_at
