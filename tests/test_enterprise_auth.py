from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from sciona_infra.api import deps
from sciona_infra.api.routers import auth as auth_mod
from sciona_infra.api.routers import scim as scim_mod


class _FakeResult:
    def __init__(self, data: Any = None, count: int | None = None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, handler, name: str):
        self._handler = handler
        self.name = name
        self.action = "select"
        self.filters: list[tuple[str, Any, Any]] = []
        self.payload: Any = None
        self.count: str | None = None
        self.range_args: tuple[int, int] | None = None

    def select(self, *_args, count: str | None = None, **_kwargs):
        self.action = "select"
        self.count = count
        return self

    def insert(self, payload: Any):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append(("eq", field, value))
        return self

    def maybe_single(self):
        self.action = f"{self.action}:maybe_single"
        return self

    def range(self, start: int, end: int):
        self.range_args = (start, end)
        return self

    async def execute(self):
        return self._handler(self)


class _FakeAuthAdmin:
    def __init__(self, *, existing_ids: set[str] | None = None):
        self.existing_ids = existing_ids or set()
        self.created_users: list[dict[str, Any]] = []
        self.updated_users: list[tuple[str, dict[str, Any]]] = []

    async def get_user_by_id(self, uid: str):
        if uid not in self.existing_ids:
            raise RuntimeError("not found")
        return SimpleNamespace(id=uid)

    async def create_user(self, attrs: dict[str, Any]):
        self.created_users.append(attrs)
        self.existing_ids.add(str(attrs["id"]))
        return SimpleNamespace(id=attrs["id"])

    async def update_user_by_id(self, uid: str, attrs: dict[str, Any]):
        self.updated_users.append((uid, attrs))
        self.existing_ids.add(uid)
        return SimpleNamespace(id=uid)


class _FakeSupabase:
    def __init__(self, handler, *, existing_ids: set[str] | None = None):
        self._handler = handler
        self.auth = SimpleNamespace(
            get_user=self._get_user,
            admin=_FakeAuthAdmin(existing_ids=existing_ids),
        )

    async def _get_user(self, _token: str):
        return SimpleNamespace(user=None)

    def table(self, name: str):
        return _FakeQuery(self._handler, name)


def _request(supabase: _FakeSupabase) -> SimpleNamespace:
    state = SimpleNamespace(supabase=supabase, supabase_admin=supabase)
    return SimpleNamespace(app=SimpleNamespace(state=state), base_url="http://testserver/")


class TestEnterpriseLogin:
    @pytest.mark.asyncio
    async def test_login_redirect_requires_config(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_SECRET", "")

        with pytest.raises(HTTPException) as exc:
            await auth_mod.enterprise_login(org_slug="acme")

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_login_redirect_builds_correct_url(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_SECRET", "secret")
        monkeypatch.setattr(
            auth_mod,
            "ENTERPRISE_CALLBACK_URL",
            "http://localhost:8000/auth/enterprise/callback",
        )
        monkeypatch.setattr(auth_mod, "AUTHENTIK_OIDC_SLUG", "sciona-platform")

        response = await auth_mod.enterprise_login(org_slug="acme")

        assert response.status_code == 307
        location = response.headers["location"]
        assert "auth.test.com" in location
        assert "client_id=test-client-id" in location
        assert "sciona-platform" in location

    @pytest.mark.asyncio
    async def test_callback_rejects_invalid_state(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_SECRET", "secret")
        auth_mod._oidc_state_store.clear()

        request = _request(_FakeSupabase(lambda _query: _FakeResult(None)))
        with pytest.raises(HTTPException) as exc:
            await auth_mod.enterprise_callback(
                code="test-code", state="bogus-state", request=request
            )
        assert exc.value.status_code == 400


class TestEnterpriseCallback:
    @pytest.mark.asyncio
    async def test_callback_creates_user_and_returns_token(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_ID", "test-client-id")
        monkeypatch.setattr(auth_mod, "AUTHENTIK_CLIENT_SECRET", "secret")
        monkeypatch.setattr(
            auth_mod,
            "ENTERPRISE_CALLBACK_URL",
            "http://localhost:8000/auth/enterprise/callback",
        )
        monkeypatch.setattr(auth_mod, "AUTHENTIK_OIDC_SLUG", "sciona-platform")
        monkeypatch.setattr(
            auth_mod,
            "uuid4",
            lambda: UUID("11111111-1111-1111-1111-111111111111"),
        )
        auth_mod._oidc_state_store.clear()
        auth_mod._oidc_state_store["state-token"] = {"org_slug": "acme"}

        created_rows: list[dict[str, Any]] = []
        table_calls: list[tuple[str, str, list[tuple[str, Any, Any]], Any]] = []

        def handler(query: _FakeQuery) -> _FakeResult:
            table_calls.append((query.name, query.action, query.filters, query.payload))
            if query.name == "users" and query.action.startswith("select"):
                if ("eq", "oidc_sub", "oidc-sub-123") in query.filters:
                    return _FakeResult(None)
                return _FakeResult(None)
            if query.name == "users" and query.action == "insert":
                created_rows.append(query.payload)
                return _FakeResult([query.payload])
            if query.name == "users" and query.action == "update":
                return _FakeResult([])
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)

        class _Response:
            def __init__(self, status_code: int, payload: dict[str, Any]):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, _url: str, **_kwargs):
                return _Response(
                    200,
                    {
                        "access_token": "oidc-access",
                        "refresh_token": "oidc-refresh",
                        "expires_in": 1800,
                    },
                )

            async def get(self, _url: str, **_kwargs):
                return _Response(
                    200,
                    {
                        "sub": "oidc-sub-123",
                        "email": "enterprise@corp.com",
                        "name": "Enterprise User",
                        "picture": "https://example.com/avatar.png",
                        "sciona_tier": "payee",
                    },
                )

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _Client())

        request = _request(supabase)
        result = await auth_mod.enterprise_callback(
            code="auth-code", state="state-token", request=request
        )

        assert result.access_token == "oidc-access"
        assert result.refresh_token == "oidc-refresh"
        assert result.expires_in == 1800
        assert created_rows[0]["user_id"] == "11111111-1111-1111-1111-111111111111"
        assert created_rows[0]["auth_provider"] == "oidc"
        assert created_rows[0]["oidc_sub"] == "oidc-sub-123"
        assert supabase.auth.admin.created_users[0]["id"] == "11111111-1111-1111-1111-111111111111"
        assert table_calls[0][2] == [("eq", "oidc_sub", "oidc-sub-123")]


class TestRequireAuthOIDC:
    @pytest.mark.asyncio
    async def test_require_auth_falls_back_to_oidc(self, monkeypatch):
        monkeypatch.setattr(deps, "AUTHENTIK_URL", "https://auth.test.com")
        monkeypatch.setattr(deps, "AUTHENTIK_OIDC_SLUG", "sciona-platform")

        user_row = {
            "user_id": "22222222-2222-2222-2222-222222222222",
            "github_id": 0,
            "github_login": "",
            "display_name": "Enterprise User",
            "avatar_url": "",
            "email": "enterprise@corp.com",
            "identity_tier": "contributor",
            "effective_tier": "general",
            "reputation_score": 0,
            "is_blacklisted": False,
            "scim_active": True,
            "oidc_sub": "oidc-sub-123",
        }

        class _Response:
            def __init__(self, status_code: int, payload: dict[str, Any]):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, _url: str, **_kwargs):
                return _Response(200, {"sub": "oidc-sub-123"})

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _Client())

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                if ("eq", "oidc_sub", "oidc-sub-123") in query.filters:
                    return _FakeResult(user_row)
                return _FakeResult(None)
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        result = await deps.require_auth(
            request, credentials=SimpleNamespace(credentials="oidc-token")
        )

        assert result.display_name == "Enterprise User"
        assert result.email == "enterprise@corp.com"

    @pytest.mark.asyncio
    async def test_require_auth_does_not_fallback_on_blacklisted(self, monkeypatch):
        monkeypatch.setattr(deps, "AUTHENTIK_URL", "https://auth.test.com")

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult(
                    {
                        "user_id": "33333333-3333-3333-3333-333333333333",
                        "github_id": 1,
                        "github_login": "alice",
                        "display_name": "Alice",
                        "avatar_url": "",
                        "email": "alice@example.com",
                        "identity_tier": "contributor",
                        "effective_tier": "general",
                        "reputation_score": 0,
                        "is_blacklisted": True,
                        "scim_active": True,
                    }
                )
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)

        async def _get_user(_token: str):
            return SimpleNamespace(user=SimpleNamespace(id="33333333-3333-3333-3333-333333333333"))

        supabase.auth.get_user = _get_user
        request = _request(supabase)

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *_args, **_kwargs):
                raise AssertionError("OIDC fallback should not be called")

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _Client())

        with pytest.raises(HTTPException) as exc:
            await deps.require_auth(
                request, credentials=SimpleNamespace(credentials="token")
            )

        assert exc.value.status_code == 403


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


class TestSCIMHelpers:
    def test_parse_username_eq(self):
        assert scim_mod._parse_scim_filter('userName eq "alice@example.com"') == (
            "email",
            "alice@example.com",
        )

    def test_parse_external_id_eq(self):
        assert scim_mod._parse_scim_filter('externalId eq "ext-123"') == (
            "scim_external_id",
            "ext-123",
        )

    def test_parse_unknown_attr_returns_none(self):
        assert scim_mod._parse_scim_filter('unknownAttr eq "value"') is None

    def test_parse_malformed_returns_none(self):
        assert scim_mod._parse_scim_filter("not a valid filter") is None

    def test_user_row_to_scim(self):
        row = {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Alice Smith",
            "email": "alice@example.com",
            "scim_external_id": "ext-1",
            "scim_active": True,
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
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

    def test_payee_group(self):
        assert scim_mod._extract_tier_from_groups(
            [{"display": "sciona-payee", "value": "group-1"}]
        ) == "payee"

    def test_default_tier(self):
        assert scim_mod._extract_tier_from_groups(
            [{"display": "sciona-contributor", "value": "group-2"}]
        ) == "contributor"

    def test_empty_groups(self):
        assert scim_mod._extract_tier_from_groups([]) == "contributor"


class TestSCIMCrud:
    @pytest.mark.asyncio
    async def test_create_user_success(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")

        created_row = {
            "user_id": "44444444-4444-4444-4444-444444444444",
            "display_name": "New User",
            "email": "new@example.com",
            "scim_external_id": "ext-new",
            "scim_active": True,
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
        }
        table_calls: list[tuple[str, str, list[tuple[str, Any, Any]], Any]] = []

        def handler(query: _FakeQuery) -> _FakeResult:
            table_calls.append((query.name, query.action, query.filters, query.payload))
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult(None)
            if query.name == "users" and query.action == "insert":
                return _FakeResult([created_row])
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)
        scim_user = scim_mod.SCIMUser(
            externalId="ext-new",
            userName="new@example.com",
            displayName="New User",
            emails=[scim_mod.SCIMEmail(value="new@example.com")],
        )

        result = await scim_mod.create_scim_user(
            user=scim_user, request=request, _token="correct-token"
        )

        assert result.displayName == "New User"
        assert supabase.auth.admin.created_users[0]["email"] == "new@example.com"
        assert table_calls[0][2] == [("eq", "scim_external_id", "ext-new")]

    @pytest.mark.asyncio
    async def test_patch_user_updates_active_and_display_name(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")

        current_row = {
            "user_id": "55555555-5555-5555-5555-555555555555",
            "display_name": "Old Name",
            "email": "old@example.com",
            "scim_external_id": "ext-55",
            "scim_active": True,
            "created_at": "",
            "updated_at": "",
        }
        updated_row = dict(current_row)
        updated_row.update(
            {
                "display_name": "New Name",
                "email": "new@example.com",
                "scim_active": False,
            }
        )
        update_payloads: list[dict[str, Any]] = []

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                if query.action == "select:maybe_single":
                    return _FakeResult(current_row if not update_payloads else updated_row)
                return _FakeResult([updated_row])
            if query.name == "users" and query.action == "update":
                update_payloads.append(query.payload)
                return _FakeResult([])
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)
        patch = scim_mod.SCIMPatchOp(
            Operations=[
                {"op": "replace", "path": "displayName", "value": "New Name"},
                {"op": "replace", "path": "active", "value": False},
            ]
        )

        result = await scim_mod.patch_scim_user(
            user_id=UUID(current_row["user_id"]),
            patch=patch,
            request=request,
            _token="correct-token",
        )

        assert result.displayName == "New Name"
        assert result.active is False
        assert update_payloads[0]["display_name"] == "New Name"
        assert update_payloads[0]["scim_active"] is False

    @pytest.mark.asyncio
    async def test_get_user_found(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")

        row = {
            "user_id": "66666666-6666-6666-6666-666666666666",
            "display_name": "Test",
            "email": "test@example.com",
            "scim_active": True,
            "created_at": "",
            "updated_at": "",
        }

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult(row)
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        result = await scim_mod.get_scim_user(
            user_id=UUID(row["user_id"]), request=request, _token="correct-token"
        )
        assert result.displayName == "Test"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult(None)
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        with pytest.raises(HTTPException) as exc:
            await scim_mod.get_scim_user(
                user_id=UUID("11111111-1111-1111-1111-111111111111"),
                request=request,
                _token="correct-token",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_deactivates_user(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")
        user_id = "77777777-7777-7777-7777-777777777777"
        update_payloads: list[dict[str, Any]] = []

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult({"user_id": user_id})
            if query.name == "users" and query.action == "update":
                update_payloads.append(query.payload)
                return _FakeResult([])
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        result = await scim_mod.delete_scim_user(
            user_id=UUID(user_id), request=request, _token="correct-token"
        )
        assert result is None
        assert update_payloads[0]["scim_active"] is False
        assert update_payloads[0]["is_blacklisted"] is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")

        def handler(query: _FakeQuery) -> _FakeResult:
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult(None)
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        with pytest.raises(HTTPException) as exc:
            await scim_mod.delete_scim_user(
                user_id=UUID("11111111-1111-1111-1111-111111111111"),
                request=request,
                _token="correct-token",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_users_with_filter(self, monkeypatch):
        monkeypatch.setattr(scim_mod, "SCIM_BEARER_TOKEN", "correct-token")
        row = {
            "user_id": "88888888-8888-8888-8888-888888888888",
            "display_name": "Filter Match",
            "email": "match@example.com",
            "scim_external_id": "ext-match",
            "scim_active": True,
            "created_at": "",
            "updated_at": "",
        }
        seen_filters: list[tuple[str, Any, Any]] = []

        def handler(query: _FakeQuery) -> _FakeResult:
            seen_filters.extend(query.filters)
            if query.name == "users" and query.action.startswith("select"):
                return _FakeResult([row], count=1)
            raise AssertionError(f"unexpected query: {query.name} {query.action}")

        supabase = _FakeSupabase(handler)
        request = _request(supabase)

        result = await scim_mod.list_scim_users(
            request=request,
            startIndex=1,
            count=50,
            filter='userName eq "match@example.com"',
            _token="correct-token",
        )

        assert result.totalResults == 1
        assert result.Resources[0].userName == "match@example.com"
        assert ("eq", "email", "match@example.com") in seen_filters
