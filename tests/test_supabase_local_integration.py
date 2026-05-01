from __future__ import annotations

import base64
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from sciona_infra.api.deps import UserProfile
from sciona_infra.api.models import AtomPublishRequest
from sciona_infra.api.routers import registry


async def _connect(db_url: str):
    import asyncpg

    return await asyncpg.connect(dsn=db_url, statement_cache_size=0)


async def _create_supabase_client(
    api_url: str,
    service_role_key: str,
):
    return _RestSupabaseClient(api_url, service_role_key)


class _RestResult:
    def __init__(self, data: Any):
        self.data = data


class _RestQuery:
    def __init__(self, client: "_RestSupabaseClient", table_name: str):
        self._client = client
        self._table_name = table_name
        self._action = "select"
        self._select = "*"
        self._payload: Any = None
        self._filters: list[tuple[str, Any]] = []
        self._maybe_single = False

    def select(self, fields: str):
        self._action = "select"
        self._select = fields
        return self

    def insert(self, payload: Any):
        self._action = "insert"
        self._payload = payload
        return self

    def update(self, payload: Any):
        self._action = "update"
        self._payload = payload
        return self

    def eq(self, field: str, value: Any):
        self._filters.append((field, value))
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    async def execute(self) -> _RestResult:
        headers = self._client.headers()
        params: dict[str, str] = {}
        if self._action == "select":
            params["select"] = self._select
            if self._maybe_single:
                params["limit"] = "1"
            for field, value in self._filters:
                params[field] = f"eq.{value}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self._client.table_url(self._table_name),
                    params=params,
                    headers=headers,
                )
        elif self._action == "insert":
            headers["Prefer"] = "return=representation"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._client.table_url(self._table_name),
                    json=self._payload,
                    headers=headers,
                )
        else:
            headers["Prefer"] = "return=representation"
            for field, value in self._filters:
                params[field] = f"eq.{value}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.patch(
                    self._client.table_url(self._table_name),
                    params=params,
                    json=self._payload,
                    headers=headers,
                )

        response.raise_for_status()
        data = response.json()
        if self._action == "select" and self._maybe_single:
            data = data[0] if data else None
        return _RestResult(data=data)


class _RestSupabaseClient:
    def __init__(self, api_url: str, service_role_key: str):
        self._api_url = api_url.rstrip("/")
        self._service_role_key = service_role_key

    def headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Content-Type": "application/json",
        }

    def table_url(self, table_name: str) -> str:
        return f"{self._api_url}/rest/v1/{table_name}"

    def table(self, table_name: str) -> _RestQuery:
        return _RestQuery(self, table_name)


async def _insert_auth_user(
    conn: Any,
    *,
    email: str,
    login: str,
    display_name: str,
    provider_id: str | None = None,
) -> dict[str, Any]:
    user_id = str(uuid4())
    raw_app_meta_data = {
        "provider": "github",
        "providers": ["github"],
    }
    raw_user_meta_data = {
        "user_name": login,
        "full_name": display_name,
        "avatar_url": f"https://example.com/{login}.png",
    }
    if provider_id is not None:
        raw_user_meta_data["provider_id"] = provider_id

    await conn.execute(
        """
        INSERT INTO auth.users (
            id,
            aud,
            role,
            email,
            email_confirmed_at,
            raw_app_meta_data,
            raw_user_meta_data,
            created_at,
            updated_at
        )
        VALUES (
            $1::uuid,
            'authenticated',
            'authenticated',
            $2,
            now(),
            $3::jsonb,
            $4::jsonb,
            now(),
            now()
        )
        """,
        user_id,
        email,
        json.dumps(raw_app_meta_data),
        json.dumps(raw_user_meta_data),
    )
    row = await conn.fetchrow(
        "SELECT * FROM public.users WHERE user_id = $1::uuid",
        user_id,
    )
    assert row is not None
    return dict(row)


async def _seed_example_members(conn: Any) -> dict[str, dict[str, Any]]:
    suffix = uuid4().hex[:8]
    provider_base = int(suffix, 16)
    owner = await _insert_auth_user(
        conn,
        email=f"owner-{suffix}@example.com",
        login=f"owner_sciona_{suffix}",
        display_name="Owner Example",
        provider_id=str(provider_base + 1),
    )
    early = await _insert_auth_user(
        conn,
        email=f"early-{suffix}@example.com",
        login=f"early_member_{suffix}",
        display_name="Early Member",
        provider_id=str(provider_base + 2),
    )
    internal = await _insert_auth_user(
        conn,
        email=f"internal-{suffix}@example.com",
        login=f"internal_member_{suffix}",
        display_name="Internal Member",
        provider_id=str(provider_base + 3),
    )
    synthetic = await _insert_auth_user(
        conn,
        email=f"synthetic-{suffix}@example.com",
        login=f"synthetic_member_{suffix}",
        display_name="Synthetic Member",
    )

    await conn.execute(
        """
        INSERT INTO public.user_entitlement_grants (
            user_id,
            source_kind,
            entitlement_tier,
            source_ref
        )
        VALUES
            ($1::uuid, 'contribution', 'early_access', 'local-test'),
            ($2::uuid, 'contribution', 'internal', 'local-test')
        """,
        early["user_id"],
        internal["user_id"],
    )

    members: dict[str, dict[str, Any]] = {}
    for name, user_id in {
        "owner": owner["user_id"],
        "early": early["user_id"],
        "internal": internal["user_id"],
        "synthetic": synthetic["user_id"],
    }.items():
        row = await conn.fetchrow(
            "SELECT * FROM public.users WHERE user_id = $1::uuid",
            user_id,
        )
        assert row is not None
        members[name] = dict(row)

    assert members["owner"]["github_id"] == provider_base + 1
    assert members["early"]["effective_tier"] == "early_access"
    assert members["internal"]["effective_tier"] == "internal"
    assert members["synthetic"]["github_id"] < 0

    return members


async def _insert_publishability_requirements(
    conn: Any,
    *,
    atom_id: str,
    version_id: str,
    owner_user_id: str,
    fqdn: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO public.atom_authors (atom_id, user_id, contribution_share)
        VALUES ($1::uuid, $2::uuid, 1.0)
        """,
        atom_id,
        owner_user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_io_specs (
            atom_id,
            version_id,
            direction,
            name,
            type_desc,
            ordinal
        )
        VALUES ($1::uuid, $2::uuid, 'input', 'signal', 'array<float>', 0)
        """,
        atom_id,
        version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_parameters (
            atom_id,
            version_id,
            name,
            position,
            kind,
            type_desc
        )
        VALUES ($1::uuid, $2::uuid, 'signal', 0, 'positional_or_keyword', 'array<float>')
        """,
        atom_id,
        version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_descriptions (
            atom_id,
            kind,
            content,
            language,
            reviewed,
            jargon_score
        )
        VALUES (
            $1::uuid,
            'dejargonized',
            'Estimate a clean signal from noisy measurements.',
            'en',
            TRUE,
            0.2
        )
        """,
        atom_id,
    )
    await conn.execute(
        """
        INSERT INTO public.references_registry (
            ref_id,
            ref_type,
            title,
            authors,
            year,
            venue
        )
        VALUES (
            'kalman-1960',
            'paper',
            'A New Approach to Linear Filtering and Prediction Problems',
            ARRAY['R. E. Kalman']::text[],
            1960,
            'Transactions of the ASME'
        )
        ON CONFLICT (ref_id) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO public.atom_references (
            atom_id,
            ref_id,
            ref_key,
            title,
            authors,
            year,
            verified
        )
        VALUES (
            $1::uuid,
            'kalman-1960',
            'kalman1960',
            'A New Approach to Linear Filtering and Prediction Problems',
            ARRAY['R. E. Kalman']::text[],
            1960,
            TRUE
        )
        """,
        atom_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_audit_rollups (
            atom_id,
            overall_verdict,
            risk_tier,
            risk_score,
            acceptability_score,
            acceptability_band,
            parity_coverage_level,
            review_status,
            trust_readiness
        )
        VALUES (
            $1::uuid,
            'trusted',
            'low',
            5,
            95,
            'acceptable_with_limits',
            'positive_and_negative',
            'complete',
            'ready'
        )
        """,
        atom_id,
    )

    publishable = await conn.fetchval(
        "SELECT public.atom_is_publishable($1::uuid)",
        atom_id,
    )
    assert publishable is True

    await conn.execute("REFRESH MATERIALIZED VIEW public.atom_audit_latest")
    await conn.execute("REFRESH MATERIALIZED VIEW public.catalog_atoms_index")

    catalog_row = await conn.fetchrow(
        """
        SELECT fqdn, overall_verdict, trust_readiness
        FROM public.catalog_atoms_served
        WHERE fqdn = $1
        """,
        fqdn,
    )
    assert catalog_row is not None
    assert catalog_row["overall_verdict"] == "trusted"
    assert catalog_row["trust_readiness"] == "ready"


async def _insert_zero_input_publishability_requirements(
    conn: Any,
    *,
    atom_id: str,
    version_id: str,
    owner_user_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO public.atom_authors (atom_id, user_id, contribution_share)
        VALUES ($1::uuid, $2::uuid, 1.0)
        """,
        atom_id,
        owner_user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_io_specs (
            atom_id,
            version_id,
            direction,
            name,
            type_desc,
            ordinal
        )
        VALUES ($1::uuid, $2::uuid, 'output', 'result', 'YawLockState', 0)
        """,
        atom_id,
        version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_descriptions (
            atom_id,
            kind,
            content,
            language,
            reviewed,
            jargon_score
        )
        VALUES (
            $1::uuid,
            'dejargonized',
            'Initialize a yaw-lock state bundle with default settings.',
            'en',
            TRUE,
            0.2
        )
        """,
        atom_id,
    )
    await conn.execute(
        """
        INSERT INTO public.references_registry (
            ref_id,
            ref_type,
            title,
            authors,
            year,
            venue
        )
        VALUES (
            'pronto-yaw-lock',
            'web',
            'Pronto yaw lock initialization notes',
            ARRAY['Sciona']::text[],
            2026,
            'Internal review'
        )
        ON CONFLICT (ref_id) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO public.atom_references (
            atom_id,
            ref_id,
            ref_key,
            title,
            authors,
            year,
            verified
        )
        VALUES (
            $1::uuid,
            'pronto-yaw-lock',
            'pronto-yaw-lock',
            'Pronto yaw lock initialization notes',
            ARRAY['Sciona']::text[],
            2026,
            TRUE
        )
        """,
        atom_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_audit_rollups (
            atom_id,
            overall_verdict,
            risk_tier,
            risk_score,
            acceptability_score,
            acceptability_band,
            parity_coverage_level,
            review_status,
            trust_readiness,
            review_semantic_verdict,
            review_developer_semantics_verdict
        )
        VALUES (
            $1::uuid,
            'trusted',
            'low',
            5,
            95,
            'acceptable_with_limits',
            'positive_and_negative',
            'approved',
            'reviewed_with_limits',
            'pass',
            'pass'
        )
        """,
        atom_id,
    )


async def _seed_publishable_atom(conn: Any) -> dict[str, str]:
    members = await _seed_example_members(conn)
    owner_user_id = str(members["owner"]["user_id"])
    atom_id = str(uuid4())
    version_id = str(uuid4())
    suffix = uuid4().hex[:8]
    fqdn = "pkg.kalman_filter_" + suffix
    search_term = "seedterm" + suffix
    description = f"Kalman filter for noisy sensor signals {search_term}"
    content_hash = "content-hash-kalman-" + suffix

    await conn.execute(
        """
        INSERT INTO public.atoms (
            atom_id,
            fqdn,
            owner_id,
            domain_tags,
            description,
            status,
            visibility_tier
        )
        VALUES (
            $1::uuid,
            $3,
            $2::uuid,
            ARRAY['signal', 'filtering']::text[],
            $4,
            'approved',
            'general'
        )
        """,
        atom_id,
        owner_user_id,
        fqdn,
        description,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_versions (
            version_id,
            atom_id,
            content_hash,
            semver,
            is_latest,
            s3_key,
            fingerprint
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            $3,
            '1.0.0',
            TRUE,
            $4,
            repeat('f', 64)
        )
        """,
        version_id,
        atom_id,
        content_hash,
        f"atoms/{content_hash}.tar.gz",
    )

    await _insert_publishability_requirements(
        conn,
        atom_id=atom_id,
        version_id=version_id,
        owner_user_id=owner_user_id,
        fqdn=fqdn,
    )

    needs_embedding = await conn.fetch(
        "SELECT atom_id FROM public.get_atoms_needing_embeddings()"
    )
    assert any(str(row["atom_id"]) == atom_id for row in needs_embedding)

    unique_primary = 1.0 + (int(suffix[:4], 16) / 65535.0)
    unique_secondary = 0.1 + (int(suffix[4:], 16) / 65535.0)

    await conn.execute(
        """
        WITH embedding AS (
            SELECT ARRAY(
                SELECT CASE
                    WHEN i = 1 THEN $4::float8
                    WHEN i = 2 THEN $5::float8
                    ELSE 0.001::float8
                END
                FROM generate_series(1, 1536) AS g(i)
            )::extensions.vector(1536) AS v
        )
        INSERT INTO public.atom_embeddings (
            atom_id,
            embedding,
            model,
            dimensions,
            input_text_hash
        )
        SELECT
            $1::uuid,
            embedding.v,
            'text-embedding-3-small',
            1536,
            public.atom_embedding_input_hash(
                $2,
                $3,
                'Estimate a clean signal from noisy measurements.',
                ARRAY['signal', 'filtering']::text[]
            )
        FROM embedding
        """,
        atom_id,
        fqdn,
        description,
        unique_primary,
        unique_secondary,
    )

    return {
        "user_id": owner_user_id,
        "atom_id": atom_id,
        "version_id": version_id,
        "fqdn": fqdn,
        "search_term": search_term,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _profile_from_member(member: dict[str, Any]) -> UserProfile:
    normalized = dict(member)
    normalized["user_id"] = str(normalized["user_id"])
    return UserProfile(**normalized)


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_phase3_phase6_objects_and_rpcs(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    try:
        assert await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
        )
        assert await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_proc
                WHERE proname = 'search_atoms_hybrid'
            )
            """
        )

        seeded = await _seed_publishable_atom(conn)

        doc = _json_value(
            await conn.fetchval(
            "SELECT public.get_atom_document($1)",
            seeded["fqdn"],
        )
        )
        assert doc["atom"]["fqdn"] == seeded["fqdn"]
        assert doc["audit_rollup"]["overall_verdict"] == "trusted"

        fts_rows = await conn.fetch(
            "SELECT fqdn, fts_rank FROM public.search_atoms_fts($1, 5, 0)",
            seeded["search_term"],
        )
        assert any(row["fqdn"] == seeded["fqdn"] for row in fts_rows)

        vector_rows = await conn.fetch(
            """
            SELECT fqdn, similarity
            FROM public.search_atoms_vector(
                (SELECT embedding FROM public.atom_embeddings WHERE atom_id = $1::uuid),
                5,
                0,
                0.0
            )
            """,
            seeded["atom_id"],
        )
        seeded_vector_rows = [
            row for row in vector_rows if row["fqdn"] == seeded["fqdn"]
        ]
        assert seeded_vector_rows
        assert seeded_vector_rows[0]["similarity"] > 0.99

        hybrid_rows = await conn.fetch(
            """
            SELECT fqdn, hybrid_score
            FROM public.search_atoms_hybrid(
                'kalman filter',
                (SELECT embedding FROM public.atom_embeddings WHERE atom_id = $1::uuid),
                'hybrid',
                5,
                0,
                1.0,
                1.0,
                60,
                0.0
            )
            """,
            seeded["atom_id"],
        )
        assert any(row["fqdn"] == seeded["fqdn"] for row in hybrid_rows)
    finally:
        await conn.close()


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_anon_catalog_and_rpc_access(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    try:
        seeded = await _seed_publishable_atom(conn)
    finally:
        await conn.close()

    import httpx

    headers = {
        "apikey": supabase_local_env["anon_key"],
        "Authorization": f"Bearer {supabase_local_env['anon_key']}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        catalog_response = await client.get(
            f"{supabase_local_env['api_url']}/rest/v1/catalog_atoms_served",
            params={"select": "fqdn,overall_verdict,risk_tier,trust_readiness"},
            headers=headers,
        )
        assert catalog_response.status_code == 200, catalog_response.text
        rows = catalog_response.json()
        assert any(row["fqdn"] == seeded["fqdn"] for row in rows)

        rpc_response = await client.post(
            f"{supabase_local_env['api_url']}/rest/v1/rpc/search_atoms_hybrid",
            content=json.dumps(
                {
                    "query_text": seeded["search_term"],
                    "mode": "fts",
                    "result_limit": 5,
                    "result_offset": 0,
                }
            ),
            headers={**headers, "Content-Type": "application/json"},
        )
        assert rpc_response.status_code == 200, rpc_response.text
        rpc_rows = rpc_response.json()
        assert any(row["fqdn"] == seeded["fqdn"] for row in rpc_rows)


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_zero_input_atom_can_be_publishable(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    try:
        members = await _seed_example_members(conn)
        owner_user_id = str(members["owner"]["user_id"])
        atom_id = str(uuid4())
        version_id = str(uuid4())

        await conn.execute(
            """
            INSERT INTO public.atoms (
                atom_id,
                fqdn,
                owner_id,
                domain_tags,
                description,
                status,
                visibility_tier
            )
            VALUES (
                $1::uuid,
                $3,
                $2::uuid,
                ARRAY['robotics', 'initialization']::text[],
                'Initialize a yaw-lock state bundle.',
                'approved',
                'general'
            )
            """,
            atom_id,
            owner_user_id,
            "pkg.zero_input_initializer_" + uuid4().hex[:8],
        )
        await conn.execute(
            """
            INSERT INTO public.atom_versions (
                version_id,
                atom_id,
                content_hash,
                semver,
                fingerprint,
                source_tar_b64,
                is_latest
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3,
                '0.1.0',
                repeat('f', 64),
                encode('zero-input', 'base64'),
                TRUE
            )
            """,
            version_id,
            atom_id,
            "zero-input-hash-" + uuid4().hex[:8],
        )

        await _insert_zero_input_publishability_requirements(
            conn,
            atom_id=atom_id,
            version_id=version_id,
            owner_user_id=owner_user_id,
        )

        assert await conn.fetchval(
            "SELECT public.atom_is_publishable($1::uuid)",
            atom_id,
        ) is True
    finally:
        await conn.close()


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_member_seed_and_publish_atom_routine(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    supabase = await _create_supabase_client(
        supabase_local_env["api_url"],
        supabase_local_env["service_role_key"],
    )
    try:
        members = await _seed_example_members(conn)
        owner = _profile_from_member(members["owner"])
        suffix = uuid4().hex[:8]

        body = AtomPublishRequest(
            fqdn=f"pkg.live_insert_{suffix}",
            semver="0.1.0",
            description="Live local atom insert",
            domain_tags=["signal", "testing"],
            source_tar_b64=base64.b64encode(
                f"live atom tarball {suffix}".encode()
            ).decode(),
            fingerprint="a" * 64,
        )

        published = await registry.publish_atom(body, user=owner, supabase=supabase)

        atom_row = await conn.fetchrow(
            """
            SELECT atom_id, owner_id, fqdn, description, is_publishable
            FROM public.atoms
            WHERE atom_id = $1::uuid
            """,
            str(published.atom_id),
        )
        assert atom_row is not None
        assert str(atom_row["owner_id"]) == str(owner.user_id)
        assert atom_row["fqdn"] == body.fqdn
        assert atom_row["description"] == body.description
        assert atom_row["is_publishable"] is False

        version_row = await conn.fetchrow(
            """
            SELECT version_id, atom_id, semver, is_latest, content_hash, fingerprint
            FROM public.atom_versions
            WHERE version_id = $1::uuid
            """,
            str(published.version_id),
        )
        assert version_row is not None
        assert str(version_row["atom_id"]) == str(published.atom_id)
        assert version_row["semver"] == body.semver
        assert version_row["is_latest"] is True
        assert version_row["content_hash"] == published.content_hash
        assert version_row["fingerprint"] == body.fingerprint

        with pytest.raises(HTTPException) as excinfo:
            await registry.publish_atom(body, user=owner, supabase=supabase)
        assert excinfo.value.status_code == 409

        await _insert_publishability_requirements(
            conn,
            atom_id=str(published.atom_id),
            version_id=str(published.version_id),
            owner_user_id=str(owner.user_id),
            fqdn=body.fqdn,
        )

        publishable_row = await conn.fetchrow(
            """
            SELECT is_publishable
            FROM public.atoms
            WHERE atom_id = $1::uuid
            """,
            str(published.atom_id),
        )
        assert publishable_row is not None
        assert publishable_row["is_publishable"] is True
    finally:
        await conn.close()


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_physics_write_plan_repeatable_loader_surfaces(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    try:
        suffix = uuid4().hex[:8]
        source_uri = f"local://physics-write-plan/{suffix}"
        payload_sha256 = ("0" * 56) + suffix
        artifact_fqdn = f"physics.write_plan_{suffix}"

        snapshot_id = await conn.fetchval(
            """
            INSERT INTO public.physics_ingest_snapshots (
                source_system,
                source_version,
                source_uri,
                adapter_name,
                adapter_version,
                license_expression,
                provenance_summary,
                payload_sha256,
                payload
            )
            VALUES (
                'physics_derivation_graph',
                'local-test',
                $1,
                'local-write-plan-test',
                '0.1.0',
                'CC0-1.0',
                'local write-plan integration test',
                $2,
                '{"kind":"write_plan"}'::jsonb
            )
            RETURNING snapshot_id
            """,
            source_uri,
            payload_sha256,
        )
        candidate_id = await conn.fetchval(
            """
            INSERT INTO public.physics_equation_candidates (
                snapshot_id,
                source_candidate_id,
                source_entity_uri,
                source_label,
                raw_formula,
                raw_formula_format,
                candidate_status,
                parse_confidence
            )
            VALUES (
                $1::uuid,
                'pdg-candidate-1',
                'local://entity/write-plan',
                'Write plan candidate',
                'F = m*a',
                'plain_text',
                'parsed',
                0.95
            )
            RETURNING candidate_id
            """,
            str(snapshot_id),
        )
        artifact_id = await conn.fetchval(
            """
            INSERT INTO public.artifacts (
                artifact_kind,
                fqdn,
                status,
                visibility_tier,
                description,
                source_kind
            )
            VALUES (
                'cdg',
                $1,
                'approved',
                'general',
                'Local physics write-plan integration artifact',
                'generated'
            )
            RETURNING artifact_id
            """,
            artifact_fqdn,
        )
        version_id = await conn.fetchval(
            """
            INSERT INTO public.artifact_versions (
                artifact_id,
                content_hash,
                semver,
                is_latest
            )
            VALUES (
                $1::uuid,
                $2,
                '0.1.0',
                TRUE
            )
            RETURNING version_id
            """,
            str(artifact_id),
            "write-plan-hash-" + suffix,
        )

        expression_ids = []
        for source_expression_id, raw_formula in (
            ("pdg-expr-source", "F = m*a"),
            ("pdg-expr-target", "a = F/m"),
        ):
            expression_ids.append(
                await conn.fetchval(
                    """
                    INSERT INTO public.artifact_symbolic_expressions (
                        artifact_id,
                        version_id,
                        candidate_id,
                        expression_kind,
                        expression_role,
                        raw_formula,
                        raw_formula_format,
                        source_expression_id,
                        parse_status,
                        review_status,
                        validation_status
                    )
                    VALUES (
                        $1::uuid,
                        $2::uuid,
                        $3::uuid,
                        'equation',
                        'primary',
                        $4,
                        'plain_text',
                        $5,
                        'parsed',
                        'automated_pass',
                        'passed'
                    )
                    RETURNING expression_id
                    """,
                    str(artifact_id),
                    str(version_id),
                    str(candidate_id),
                    raw_formula,
                    source_expression_id,
                )
            )

        relationship_id = await conn.fetchval(
            """
            INSERT INTO public.artifact_relationships (
                source_expression_id,
                target_expression_id,
                relationship_kind,
                source_node_id,
                target_node_id,
                inference_rule_id,
                source_kind,
                confidence
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                'derives_from',
                'node-source',
                'node-target',
                'rule-1',
                'physics_derivation_graph',
                0.5
            )
            ON CONFLICT (
                source_kind,
                relationship_kind,
                source_expression_id,
                target_expression_id,
                source_node_id,
                target_node_id,
                inference_rule_id
            )
            WHERE source_kind = 'physics_derivation_graph'
              AND source_expression_id IS NOT NULL
              AND target_expression_id IS NOT NULL
              AND source_node_id <> ''
              AND target_node_id <> ''
              AND inference_rule_id <> ''
            DO UPDATE SET confidence = EXCLUDED.confidence
            RETURNING relationship_id
            """,
            str(expression_ids[0]),
            str(expression_ids[1]),
        )
        repeated_relationship_id = await conn.fetchval(
            """
            INSERT INTO public.artifact_relationships (
                source_expression_id,
                target_expression_id,
                relationship_kind,
                source_node_id,
                target_node_id,
                inference_rule_id,
                source_kind,
                confidence
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                'derives_from',
                'node-source',
                'node-target',
                'rule-1',
                'physics_derivation_graph',
                0.9
            )
            ON CONFLICT (
                source_kind,
                relationship_kind,
                source_expression_id,
                target_expression_id,
                source_node_id,
                target_node_id,
                inference_rule_id
            )
            WHERE source_kind = 'physics_derivation_graph'
              AND source_expression_id IS NOT NULL
              AND target_expression_id IS NOT NULL
              AND source_node_id <> ''
              AND target_node_id <> ''
              AND inference_rule_id <> ''
            DO UPDATE SET confidence = EXCLUDED.confidence
            RETURNING relationship_id
            """,
            str(expression_ids[0]),
            str(expression_ids[1]),
        )
        assert str(repeated_relationship_id) == str(relationship_id)
        assert (
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM public.artifact_relationships
                WHERE source_kind = 'physics_derivation_graph'
                  AND source_node_id = 'node-source'
                  AND target_node_id = 'node-target'
                  AND inference_rule_id = 'rule-1'
                """
            )
            == 1
        )
        relationship_confidence = await conn.fetchval(
            """
            SELECT confidence
            FROM public.artifact_relationships
            WHERE relationship_id = $1::uuid
            """,
            str(relationship_id),
        )
        assert abs(relationship_confidence - 0.9) < 0.000001

        bound_id = await conn.fetchval(
            """
            INSERT INTO public.artifact_validity_bounds (
                artifact_id,
                version_id,
                expression_id,
                scope,
                bound_kind,
                variable_name,
                lower_value,
                upper_value,
                evidence_ref_key,
                confidence
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                'expression',
                'domain',
                'm',
                0.0,
                NULL,
                'pdg-bound-1',
                'medium'
            )
            ON CONFLICT (
                expression_id,
                scope,
                bound_kind,
                variable_name,
                evidence_ref_key
            )
            WHERE expression_id IS NOT NULL
              AND evidence_ref_key <> ''
            DO UPDATE SET confidence = EXCLUDED.confidence
            RETURNING bound_id
            """,
            str(artifact_id),
            str(version_id),
            str(expression_ids[0]),
        )
        repeated_bound_id = await conn.fetchval(
            """
            INSERT INTO public.artifact_validity_bounds (
                artifact_id,
                version_id,
                expression_id,
                scope,
                bound_kind,
                variable_name,
                lower_value,
                upper_value,
                evidence_ref_key,
                confidence
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                'expression',
                'domain',
                'm',
                0.0,
                NULL,
                'pdg-bound-1',
                'high'
            )
            ON CONFLICT (
                expression_id,
                scope,
                bound_kind,
                variable_name,
                evidence_ref_key
            )
            WHERE expression_id IS NOT NULL
              AND evidence_ref_key <> ''
            DO UPDATE SET confidence = EXCLUDED.confidence
            RETURNING bound_id
            """,
            str(artifact_id),
            str(version_id),
            str(expression_ids[0]),
        )
        assert str(repeated_bound_id) == str(bound_id)
        assert (
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM public.artifact_validity_bounds
                WHERE expression_id = $1::uuid
                  AND evidence_ref_key = 'pdg-bound-1'
                """,
                str(expression_ids[0]),
            )
            == 1
        )
        assert await conn.fetchval(
            """
            SELECT confidence
            FROM public.artifact_validity_bounds
            WHERE bound_id = $1::uuid
            """,
            str(bound_id),
        ) == "high"

        source_id_map = _json_value(
            await conn.fetchval(
                """
                SELECT public.physics_symbolic_write_plan_source_id_map(
                    'physics_derivation_graph',
                    'local-test'
                )
                """
            )
        )
        matched = [
            row
            for row in source_id_map
            if row["source_uri"] == source_uri
            and row["source_candidate_id"] == "pdg-candidate-1"
            and row["source_expression_id"]
            in {"pdg-expr-source", "pdg-expr-target"}
        ]
        assert {row["fqdn"] for row in matched} == {artifact_fqdn}
        assert {row["semver"] for row in matched} == {"0.1.0"}
    finally:
        await conn.close()
