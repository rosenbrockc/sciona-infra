from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx


async def connect(db_url: str):
    import asyncpg

    return await asyncpg.connect(dsn=db_url, statement_cache_size=0)


class RestResult:
    def __init__(self, data: Any = None, count: int | None = None):
        self.data = data
        self.count = count


class RestRpcQuery:
    def __init__(self, client: "LiveSupabaseClient", name: str, params: dict[str, Any]):
        self._client = client
        self._name = name
        self._params = params

    async def execute(self) -> RestResult:
        response = await self._client._request(
            "POST",
            f"/rest/v1/rpc/{self._name}",
            headers=self._client.headers(),
            json=self._params,
        )
        data = response.json() if response.content else None
        return RestResult(data=data)


class RestQuery:
    def __init__(self, client: "LiveSupabaseClient", table_name: str):
        self._client = client
        self._table_name = table_name
        self._action = "select"
        self._select = "*"
        self._payload: Any = None
        self._filters: list[tuple[str, Any, Any]] = []
        self._maybe_single = False
        self._count: str | None = None
        self._orderings: list[tuple[str, bool]] = []
        self._range: tuple[int, int] | None = None
        self._limit: int | None = None

    def select(self, fields: str, count: str | None = None):
        self._action = "select"
        self._select = fields
        self._count = count
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
        self._filters.append(("eq", field, value))
        return self

    def contains(self, field: str, value: Any):
        self._filters.append(("contains", field, value))
        return self

    def or_(self, clause: str):
        self._filters.append(("or", clause, None))
        return self

    def in_(self, field: str, values: list[Any]):
        self._filters.append(("in", field, values))
        return self

    def order(self, field: str, desc: bool = False):
        self._orderings.append((field, desc))
        return self

    def range(self, start: int, end: int):
        self._range = (start, end)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    async def execute(self) -> RestResult:
        headers = self._client.headers()
        params: dict[str, str] = {}
        if self._count:
            headers["Prefer"] = f"count={self._count}"

        if self._action == "select":
            params["select"] = self._select
            if self._limit is not None:
                params["limit"] = str(self._limit)
            if self._maybe_single and self._limit is None:
                params["limit"] = "1"
            for filter_kind, field, value in self._filters:
                if filter_kind == "eq":
                    params[field] = f"eq.{_scalar(value)}"
                elif filter_kind == "contains":
                    params[field] = f"cs.{json.dumps(value, separators=(',', ':'))}"
                elif filter_kind == "or":
                    params["or"] = f"({field})"
                elif filter_kind == "in":
                    params[field] = f"in.({','.join(_scalar(item) for item in value)})"
            if self._orderings:
                params["order"] = ",".join(
                    f"{field}.{'desc' if desc else 'asc'}" for field, desc in self._orderings
                )
            request_headers = dict(headers)
            if self._range is not None:
                start, end = self._range
                request_headers["Range-Unit"] = "items"
                request_headers["Range"] = f"{start}-{end}"
            response = await self._client._request(
                "GET",
                f"/rest/v1/{self._table_name}",
                headers=request_headers,
                params=params,
            )
        elif self._action == "insert":
            headers["Prefer"] = _append_prefer(headers.get("Prefer"), "return=representation")
            response = await self._client._request(
                "POST",
                f"/rest/v1/{self._table_name}",
                headers=headers,
                json=self._payload,
            )
        else:
            headers["Prefer"] = _append_prefer(headers.get("Prefer"), "return=representation")
            for filter_kind, field, value in self._filters:
                if filter_kind != "eq":
                    raise ValueError(f"Unsupported update filter: {filter_kind}")
                params[field] = f"eq.{_scalar(value)}"
            response = await self._client._request(
                "PATCH",
                f"/rest/v1/{self._table_name}",
                headers=headers,
                params=params,
                json=self._payload,
            )

        data = response.json() if response.content else None
        if self._action == "select" and self._maybe_single and isinstance(data, list):
            data = data[0] if data else None
        return RestResult(data=data, count=_content_range_count(response.headers))


class _AuthApi:
    def __init__(self, client: "LiveSupabaseClient"):
        self._client = client

    async def get_user(self, token: str):
        response = await self._client._request(
            "GET",
            "/auth/v1/user",
            headers={
                "apikey": self._client._service_role_key,
                "Authorization": f"Bearer {token}",
            },
        )
        payload = response.json()
        return SimpleNamespace(user=SimpleNamespace(id=payload["id"], email=payload.get("email", "")))


class LiveSupabaseClient:
    def __init__(self, api_url: str, service_role_key: str):
        self._api_url = api_url.rstrip("/")
        self._service_role_key = service_role_key
        self.auth = _AuthApi(self)

    def headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Content-Type": "application/json",
        }

    def table(self, table_name: str) -> RestQuery:
        return RestQuery(self, table_name)

    def rpc(self, name: str, params: dict[str, Any]) -> RestRpcQuery:
        return RestRpcQuery(self, name, params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{self._api_url}{path}",
                headers=headers,
                params=params,
                json=_json_safe(json),
            )
        response.raise_for_status()
        return response


@dataclass(slots=True)
class SeedUser:
    user_id: str
    email: str
    password: str
    token: str
    github_login: str
    display_name: str
    identity_tier: str


@dataclass(slots=True)
class RuntimeSeed:
    suffix: str
    owner: SeedUser
    architect: SeedUser
    reviewer: SeedUser
    organization_id: str
    repo_id: str
    atom_id: str
    atom_version_id: str
    atom_fqdn: str
    atom_content_hash: str
    comparison_atom_id: str
    comparison_version_id: str
    comparison_fqdn: str
    comparison_content_hash: str
    search_term: str
    benchmark_suite_id: str
    settled_bounty_id: str
    settled_submission_id: str


async def create_live_auth_user(
    conn: Any,
    *,
    api_url: str,
    anon_key: str,
    service_role_key: str,
    email: str,
    password: str,
    login: str,
    display_name: str,
    identity_tier: str,
) -> SeedUser:
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {
            "user_name": login,
            "full_name": display_name,
            "avatar_url": f"https://example.com/{login}.png",
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        create_response = await client.post(
            f"{api_url.rstrip('/')}/auth/v1/admin/users",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        create_response.raise_for_status()
        created = create_response.json()
        user_obj = created.get("user", created)
        user_id = str(user_obj["id"])

        token_response = await client.post(
            f"{api_url.rstrip('/')}/auth/v1/token?grant_type=password",
            headers={
                "apikey": anon_key,
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]

    row = await wait_for_public_user(conn, user_id)
    if row is None:
        raise AssertionError(f"public.users row never appeared for {user_id}")

    await conn.execute(
        """
        UPDATE public.users
           SET github_login = $2,
               display_name = $3,
               avatar_url = $4,
               email = $5,
               identity_tier = $6,
               reputation_score = 25,
               updated_at = now()
         WHERE user_id = $1::uuid
        """,
        user_id,
        login,
        display_name,
        f"https://example.com/{login}.png",
        email,
        identity_tier,
    )

    return SeedUser(
        user_id=user_id,
        email=email,
        password=password,
        token=token,
        github_login=login,
        display_name=display_name,
        identity_tier=identity_tier,
    )


async def wait_for_public_user(
    conn: Any,
    user_id: str,
    *,
    timeout_s: float = 10.0,
) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        row = await conn.fetchrow(
            "SELECT * FROM public.users WHERE user_id = $1::uuid",
            user_id,
        )
        if row is not None:
            return dict(row)
        await asyncio.sleep(0.25)
    return None


async def seed_runtime_dataset(
    conn: Any,
    *,
    api_url: str,
    anon_key: str,
    service_role_key: str,
) -> RuntimeSeed:
    suffix = uuid4().hex[:8]
    owner = await create_live_auth_user(
        conn,
        api_url=api_url,
        anon_key=anon_key,
        service_role_key=service_role_key,
        email=f"owner-{suffix}@example.com",
        password=f"OwnerPass!{suffix}",
        login=f"owner_runtime_{suffix}",
        display_name="Owner Runtime",
        identity_tier="payee",
    )
    architect = await create_live_auth_user(
        conn,
        api_url=api_url,
        anon_key=anon_key,
        service_role_key=service_role_key,
        email=f"architect-{suffix}@example.com",
        password=f"ArchitectPass!{suffix}",
        login=f"architect_runtime_{suffix}",
        display_name="Architect Runtime",
        identity_tier="contributor",
    )
    reviewer = await create_live_auth_user(
        conn,
        api_url=api_url,
        anon_key=anon_key,
        service_role_key=service_role_key,
        email=f"reviewer-{suffix}@example.com",
        password=f"ReviewerPass!{suffix}",
        login=f"reviewer_runtime_{suffix}",
        display_name="Reviewer Runtime",
        identity_tier="payee",
    )

    organization_id = str(uuid4())
    repo_id = str(uuid4())
    atom_id = str(uuid4())
    atom_version_id = str(uuid4())
    comparison_atom_id = str(uuid4())
    comparison_version_id = str(uuid4())
    settled_bounty_id = str(uuid4())
    settled_submission_id = str(uuid4())
    benchmark_suite_id = f"suite-{suffix}"
    search_term = f"runtime-search-{suffix}"
    atom_fqdn = f"pkg.runtime_seed_{suffix}"
    comparison_fqdn = f"pkg.runtime_peer_{suffix}"
    atom_content_hash = hashlib.sha256(f"atom-main-{suffix}".encode()).hexdigest()
    comparison_content_hash = hashlib.sha256(f"atom-peer-{suffix}".encode()).hexdigest()
    ref_id = f"kalman-{suffix}"
    atom_versions_payload = json.dumps(
        {
            atom_fqdn: atom_content_hash,
            comparison_fqdn: comparison_content_hash,
        }
    )
    receipt_payload = json.dumps(
        {
            "metric_name": "accuracy",
            "metric_value": 0.97,
            "split_hash": f"split-{suffix}",
            "artifact": "receipt",
        }
    )

    await conn.execute(
        """
        INSERT INTO public.roles (role_name, grants_tier, description)
        VALUES
            ('Maintainer', 'internal', 'Maintains runtime catalog'),
            ('Paid Member', 'early_access', 'Paid access member')
        ON CONFLICT (role_name) DO UPDATE
        SET grants_tier = EXCLUDED.grants_tier,
            description = EXCLUDED.description
        """
    )
    await conn.execute(
        """
        INSERT INTO public.organizations (organization_id, name, entitlement_tier, membership_status)
        VALUES ($1::uuid, $2, 'internal', 'active')
        """,
        organization_id,
        f"Runtime Org {suffix}",
    )
    await conn.execute(
        "INSERT INTO public.organization_email_domains (organization_id, email_domain) VALUES ($1::uuid, $2)",
        organization_id,
        f"runtime-{suffix}.example.com",
    )
    await conn.execute(
        """
        INSERT INTO public.organization_memberships (organization_id, user_id, membership_source)
        VALUES ($1::uuid, $2::uuid, 'manual'), ($1::uuid, $3::uuid, 'manual')
        """,
        organization_id,
        owner.user_id,
        reviewer.user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.user_memberships (
            user_id,
            membership_kind,
            entitlement_tier,
            stripe_customer_id,
            stripe_subscription_id
        )
        VALUES ($1::uuid, 'paid', 'early_access', $2, $3)
        """,
        owner.user_id,
        f"cus_{suffix}",
        f"sub_{suffix}",
    )
    await conn.execute(
        """
        INSERT INTO public.user_entitlement_grants (
            user_id,
            source_kind,
            entitlement_tier,
            source_ref,
            created_by
        )
        VALUES ($1::uuid, 'organization_membership', 'internal', 'runtime-seed', $2::uuid)
        """,
        reviewer.user_id,
        owner.user_id,
    )
    await conn.execute(
        "INSERT INTO public.user_role_assignments (user_id, role_name, granted_by) VALUES ($1::uuid, 'Maintainer', $2::uuid)",
        owner.user_id,
        reviewer.user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.contribution_events (
            user_id,
            event_kind,
            entity_kind,
            entity_fqdn,
            source,
            source_ref,
            notes
        )
        VALUES ($1::uuid, 'atom_update', 'atom', $2, 'admin', 'seed', 'runtime coverage seed')
        """,
        owner.user_id,
        atom_fqdn,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_source_repositories (
            source_repo_id,
            repo_name,
            repo_url,
            namespace_root,
            namespace_path
        )
        VALUES ($1::uuid, $2, $3, 'sciona.atoms', 'smoke_test')
        """,
        repo_id,
        f"runtime-repo-{suffix}",
        f"https://example.com/runtime-repo-{suffix}.git",
    )
    await conn.execute(
        """
        INSERT INTO public.atoms (
            atom_id,
            fqdn,
            owner_id,
            source_repo_id,
            namespace_path,
            domain_tags,
            description,
            status,
            visibility_tier,
            source_package,
            source_module_path,
            source_symbol,
            source_kind,
            stateful_kind,
            is_stochastic,
            is_ffi
        )
        VALUES (
            $1::uuid,
            $2,
            $3::uuid,
            $4::uuid,
            'runtime',
            ARRAY['signal','baseline']::text[],
            $5,
            'approved',
            'general',
            'runtime_pkg',
            'runtime.module',
            'RuntimeSeed',
            'hand_written',
            'none',
            FALSE,
            FALSE
        )
        """,
        atom_id,
        atom_fqdn,
        owner.user_id,
        repo_id,
        f"Runtime atom tuned for {search_term}",
    )
    await conn.execute(
        """
        INSERT INTO public.atoms (
            atom_id,
            fqdn,
            owner_id,
            source_repo_id,
            namespace_path,
            domain_tags,
            description,
            status,
            visibility_tier,
            source_package,
            source_module_path,
            source_symbol,
            source_kind,
            stateful_kind,
            is_stochastic,
            is_ffi
        )
        VALUES (
            $1::uuid,
            $2,
            $3::uuid,
            $4::uuid,
            'runtime',
            ARRAY['signal','comparison']::text[],
            $5,
            'approved',
            'general',
            'runtime_pkg',
            'runtime.peer',
            'RuntimePeer',
            'hand_written',
            'none',
            FALSE,
            FALSE
        )
        """,
        comparison_atom_id,
        comparison_fqdn,
        architect.user_id,
        repo_id,
        f"Peer runtime atom for {search_term}",
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
        VALUES
            ($1::uuid, $2::uuid, $3, '1.0.0', TRUE, $4, repeat('a', 64)),
            ($5::uuid, $6::uuid, $7, '0.9.0', TRUE, $8, repeat('b', 64))
        """,
        atom_version_id,
        atom_id,
        atom_content_hash,
        f"atoms/{atom_content_hash}.tar.gz",
        comparison_version_id,
        comparison_atom_id,
        comparison_content_hash,
        f"atoms/{comparison_content_hash}.tar.gz",
    )
    await conn.execute(
        "INSERT INTO public.atom_authors (atom_id, user_id, contribution_share) VALUES ($1::uuid, $2::uuid, 1.0), ($3::uuid, $4::uuid, 1.0)",
        atom_id,
        owner.user_id,
        comparison_atom_id,
        architect.user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.hyperparams (
            atom_id,
            name,
            kind,
            default_value,
            min_value,
            max_value,
            step_value,
            semantic_role,
            status
        )
        VALUES ($1::uuid, 'window_size', 'int', '32'::jsonb, '8'::jsonb, '128'::jsonb, '8'::jsonb, 'smoothing', 'approved')
        """,
        atom_id,
    )
    await conn.execute(
        "INSERT INTO public.atom_io_specs (atom_id, version_id, direction, name, type_desc, ordinal) VALUES ($1::uuid, $2::uuid, 'input', 'signal', 'array<float>', 0), ($1::uuid, $2::uuid, 'output', 'baseline', 'array<float>', 1)",
        atom_id,
        atom_version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_parameters (
            atom_id,
            version_id,
            name,
            position,
            kind,
            type_desc,
            technical_description,
            dejargonized_description,
            constraints_json
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            'signal',
            0,
            'positional_or_keyword',
            'array<float>',
            'Raw signal samples',
            'A list of numbers that represent the signal',
            '{"min_length": 16}'::jsonb
        )
        """,
        atom_id,
        atom_version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_descriptions (atom_id, kind, content, language, reviewed, jargon_score, generated_by)
        VALUES ($1::uuid, 'dejargonized', 'Estimate a stable baseline from a noisy signal.', 'en', TRUE, 0.2, 'runtime-seed')
        """,
        atom_id,
    )
    await conn.execute(
        """
        INSERT INTO public.references_registry (ref_id, ref_type, title, authors, year, venue, url, bibtex_key)
        VALUES ($1, 'paper', 'Runtime Seeding for Baseline Validation', ARRAY['A. Example']::text[], 2026, 'Sciona Journal', 'https://example.com/runtime-seed', 'runtimeSeed2026')
        """,
        ref_id,
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
            url,
            relevance_note,
            confidence,
            matched_nodes,
            source,
            verified
        )
        VALUES (
            $1::uuid,
            $2,
            'runtimeSeed2026',
            'Runtime Seeding for Baseline Validation',
            ARRAY['A. Example']::text[],
            2026,
            'https://example.com/runtime-seed',
            'Primary method reference',
            'high',
            ARRAY['normalize','fit']::text[],
            'manual',
            TRUE
        )
        """,
        atom_id,
        ref_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_uncertainty_estimates (
            atom_id,
            version_id,
            mode,
            scalar_factor,
            confidence,
            n_trials,
            epsilon,
            input_regime,
            notes
        )
        VALUES ($1::uuid, $2::uuid, 'empirical', 0.08, 0.95, 24, 0.001, 'local-dev', 'Seeded runtime uncertainty')
        """,
        atom_id,
        atom_version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_verification_matches (
            atom_id,
            version_id,
            predicate_id,
            predicate_statement,
            informal_desc,
            candidate_name,
            candidate_source_lib,
            candidate_score,
            retrieval_method,
            verified,
            verification_level,
            proof_term,
            all_candidates,
            all_verifications
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            'baseline-preservation',
            'Preserves baseline trend under additive noise',
            'Seed verification candidate',
            'BaselineVerifier',
            'sciona.verify',
            0.98,
            'semantic',
            TRUE,
            'kernel_proof',
            'proof runtime_seed',
            '[{"candidate":"BaselineVerifier"}]'::jsonb,
            '[{"level":"kernel_proof"}]'::jsonb
        )
        """,
        atom_id,
        atom_version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_audit_evidence (
            atom_id,
            version_id,
            audit_type,
            passed,
            details,
            source_kind,
            runner_version,
            run_duration_ms,
            source_revision,
            upstream_version
        )
        VALUES ($1::uuid, $2::uuid, 'smoke_test', TRUE, '{"coverage":"full"}'::jsonb, 'automated', '1.0.0', 1250, 'seed-rev', '2026.04')
        """,
        atom_id,
        atom_version_id,
    )
    await conn.execute(
        """
        INSERT INTO public.atom_audit_rollups (
            atom_id,
            overall_verdict,
            structural_status,
            runtime_status,
            semantic_status,
            developer_semantics_status,
            risk_tier,
            risk_score,
            risk_dimensions,
            risk_reasons,
            acceptability_score,
            acceptability_band,
            parity_coverage_level,
            parity_test_status,
            parity_fixture_count,
            parity_case_count,
            review_status,
            review_semantic_verdict,
            review_developer_semantics_verdict,
            review_limitations,
            review_required_actions,
            trust_readiness,
            trust_blockers
        )
        VALUES (
            $1::uuid,
            'trusted',
            'verified',
            'verified',
            'verified',
            'verified',
            'low',
            3,
            '{"runtime":1,"semantic":1}'::jsonb,
            ARRAY['seeded']::text[],
            97,
            'acceptable_with_limits',
            'positive_and_negative',
            'complete',
            2,
            12,
            'complete',
            'pass',
            'pass',
            ARRAY['none']::text[],
            ARRAY[]::text[],
            'ready',
            ARRAY[]::text[]
        )
        """,
        atom_id,
    )
    await conn.execute(
        "INSERT INTO public.atom_benchmarks (version_id, benchmark_name, metric_name, metric_value, dataset_tag) VALUES ($1::uuid, $2, 'accuracy', 0.97, 'seeded-dev')",
        atom_version_id,
        benchmark_suite_id,
    )
    await conn.execute(
        """
        WITH embedding AS (
            SELECT ARRAY(
                SELECT CASE WHEN i = 1 THEN 1.0::float8 ELSE 0.001::float8 END
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
                'Estimate a stable baseline from a noisy signal.',
                ARRAY['signal','baseline']::text[]
            )
        FROM embedding
        """,
        atom_id,
        atom_fqdn,
        f"Runtime atom tuned for {search_term}",
    )
    await conn.execute(
        "INSERT INTO public.embedding_refresh_queue (atom_id, reason, status, error_message, attempts) VALUES ($1::uuid, 'content_changed', 'completed', '', 1)",
        comparison_atom_id,
    )
    await conn.execute(
        "INSERT INTO public.fuzz_results (atom_fqdn, content_hash, strategy, passed, failures, inputs_tested, runtime_ms) VALUES ($1, $2, 'property_based', TRUE, '[]'::jsonb, 64, 220)",
        atom_fqdn,
        atom_content_hash,
    )
    await conn.execute(
        """
        INSERT INTO public.behavioral_equivalence_flags (
            atom_a_fqdn,
            atom_a_hash,
            atom_b_fqdn,
            atom_b_hash,
            match_ratio,
            sample_size,
            reviewed,
            reviewer_id,
            disposition
        )
        VALUES ($1, $2, $3, $4, 0.94, 48, TRUE, $5::uuid, 'common_algorithm')
        """,
        atom_fqdn,
        atom_content_hash,
        comparison_fqdn,
        comparison_content_hash,
        reviewer.user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.benchmark_suites (
            benchmark_id,
            domain_tags,
            description,
            dataset_s3_key,
            metric_names,
            curation_source,
            proposer_id,
            vote_count,
            status
        )
        VALUES ($1, ARRAY['signal']::text[], 'Runtime benchmark suite', 'benchmarks/runtime.parquet', ARRAY['accuracy']::text[], 'foundation', $2::uuid, 1, 'active')
        """,
        benchmark_suite_id,
        reviewer.user_id,
    )
    await conn.execute(
        "INSERT INTO public.benchmark_votes (benchmark_id, voter_id, vote) VALUES ($1, $2::uuid, 'approve')",
        benchmark_suite_id,
        owner.user_id,
    )
    await conn.execute(
        """
        INSERT INTO public.discipline_repos (
            repo_id,
            repo_url,
            webhook_secret,
            domain_tags,
            maintainer_ids,
            last_synced_commit,
            status
        )
        VALUES ($1::uuid, $2, 'whsec-runtime', ARRAY['signal']::text[], ARRAY[$3::uuid]::uuid[], $4, 'active')
        """,
        str(uuid4()),
        f"https://example.com/discipline-{suffix}.git",
        reviewer.user_id,
        f"commit-{suffix}",
    )
    await conn.execute(
        """
        INSERT INTO public.bounties (
            bounty_id,
            principal_id,
            title,
            escrow_amount,
            status,
            deadline,
            tier,
            verification_budget,
            verifications_used,
            config_yml,
            flare_payload,
            public_split_hash,
            blind_split_hash,
            cancellation_fee
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            $3,
            250.00,
            'settled',
            now() + interval '14 days',
            'standard',
            5,
            1,
            '{"min_metric_value":0.95}'::jsonb,
            '{"mode":"runtime"}'::jsonb,
            $4,
            $5,
            0
        )
        """,
        settled_bounty_id,
        reviewer.user_id,
        f"Runtime bounty {suffix}",
        f"public-{suffix}",
        f"blind-{suffix}",
    )
    await conn.execute(
        "INSERT INTO public.dataset_splits (bounty_id, unit_key, partition) VALUES ($1::uuid, $2, 'public'), ($1::uuid, $3, 'blind')",
        settled_bounty_id,
        f"unit-public-{suffix}",
        f"unit-blind-{suffix}",
    )
    await conn.execute(
        "INSERT INTO public.principal_targets (bounty_id, metric_name, target_value, set_by) VALUES ($1::uuid, 'accuracy', 0.95, $2::uuid)",
        settled_bounty_id,
        reviewer.user_id,
    )
    await conn.execute(
        "INSERT INTO public.verification_budgets (bounty_id, tier, total_slots, used_slots, cost_per_extra, overhead_deposit, overhead_used) VALUES ($1::uuid, 'standard', 5, 1, 25.00, 10.00, 2.50)",
        settled_bounty_id,
    )
    await conn.execute(
        """
        INSERT INTO public.submissions (
            submission_id,
            bounty_id,
            architect_id,
            cdg_hash,
            atom_versions,
            receipt_s3,
            receipt_json,
            claimed_metric_name,
            claimed_metric_value,
            verified_metric_value,
            verification_status,
            is_winner,
            submitted_at,
            verified_at
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            $3::uuid,
            $4,
            $5::jsonb,
            '',
            $6::jsonb,
            'accuracy',
            0.97,
            0.97,
            'blind_verified',
            TRUE,
            now() - interval '1 day',
            now() - interval '12 hours'
        )
        """,
        settled_submission_id,
        settled_bounty_id,
        architect.user_id,
        f"cdg-{suffix}",
        atom_versions_payload,
        receipt_payload,
    )
    await conn.execute(
        """
        INSERT INTO public.verification_runs (
            bounty_id,
            submission_id,
            split_type,
            status,
            metric_values,
            output_hash,
            execution_time_s,
            peak_memory_bytes,
            is_deterministic,
            sandbox_job_id,
            slot_consumed,
            started_at,
            completed_at
        )
        VALUES (
            $1::uuid,
            $2::uuid,
            'blind',
            'completed',
            '{"accuracy":0.97}'::jsonb,
            $3,
            3.2,
            1048576,
            TRUE,
            $4,
            TRUE,
            now() - interval '13 hours',
            now() - interval '12 hours'
        )
        """,
        settled_bounty_id,
        settled_submission_id,
        f"output-{suffix}",
        f"job-{suffix}",
    )
    await conn.execute(
        """
        INSERT INTO public.execution_receipts (
            submission_id,
            bounty_id,
            cdg_hash,
            atom_versions,
            split_hash,
            output_hash,
            metric_name,
            metric_value,
            ageom_version,
            ssh_signature,
            ssh_public_key,
            verified,
            receipt_timestamp
        )
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6, 'accuracy', 0.97, '0.1.0', 'ssh-signature', 'ssh-public-key', TRUE, now() - interval '12 hours')
        """,
        settled_submission_id,
        settled_bounty_id,
        f"cdg-{suffix}",
        atom_versions_payload,
        f"split-{suffix}",
        f"output-{suffix}",
    )
    await conn.execute(
        "INSERT INTO public.bounty_best_scores (bounty_id, metric_name, best_value, best_submission_id, is_baseline) VALUES ($1::uuid, 'accuracy', 0.97, $2::uuid, FALSE)",
        settled_bounty_id,
        settled_submission_id,
    )
    await conn.execute(
        "INSERT INTO public.payouts (bounty_id, user_id, role, amount, shapley_value, stripe_transfer_id, status) VALUES ($1::uuid, $2::uuid, 'architect', 162.50, '0.65', 'tr_architect', 'transferred'), ($1::uuid, $3::uuid, 'originator', 75.00, '0.30', 'tr_originator', 'transferred')",
        settled_bounty_id,
        architect.user_id,
        owner.user_id,
    )
    await conn.execute(
        "INSERT INTO public.settlement_payouts (bounty_id, recipient_id, role, amount, stripe_transfer_id, atom_fqdn, cdg_hash) VALUES ($1::uuid, $2, 'architect', 162.50, 'tr_architect', $4, $5), ($1::uuid, $3, 'originator', 75.00, 'tr_originator', $4, $5)",
        settled_bounty_id,
        architect.user_id,
        owner.user_id,
        atom_fqdn,
        f"cdg-{suffix}",
    )

    # Seed additional bounties at varying escrow tiers and statuses
    _extra_bounties = [
        ("Optimal sparse-attention kernel for 128K context", 50000.00, "open", "premium"),
        ("Differentiable physics solver for fluid sim", 25000.00, "open", "premium"),
        ("Low-rank adaptation for domain-specific LLM fine-tuning", 10000.00, "submitted", "standard"),
        ("Verified RLHF reward model calibration", 5000.00, "verification", "standard"),
        ("Sub-quadratic graph neural network aggregator", 5000.00, "open", "standard"),
        ("Efficient KV-cache compression for long-context inference", 2500.00, "submitted", "standard"),
        ("Privacy-preserving federated gradient aggregation", 1500.00, "settled", "standard"),
        ("Numerically stable softmax for mixed-precision training", 750.00, "settled", "standard"),
        ("Symbolic regression benchmark atom", 500.00, "cancelled", "basic"),
        ("Bayesian hyperparameter sampler atom", 250.00, "expired", "basic"),
    ]
    for _title, _escrow, _status, _tier in _extra_bounties:
        _bid = str(uuid4())
        await conn.execute(
            """
            INSERT INTO public.bounties (
                bounty_id, principal_id, title, escrow_amount, status,
                deadline, tier, verification_budget, verifications_used,
                config_yml, flare_payload, public_split_hash, blind_split_hash,
                cancellation_fee
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5,
                now() + interval '30 days', $6, 5, 0,
                '{"min_metric_value":0.90}'::jsonb, '{"mode":"runtime"}'::jsonb,
                $7, $8, 0
            )
            """,
            _bid,
            reviewer.user_id,
            _title,
            _escrow,
            _status,
            _tier,
            f"pub-{_bid[:8]}",
            f"blind-{_bid[:8]}",
        )

    # Seed additional architects with winning submissions and payouts
    _architect_names = [
        ("dr_chen", "Dr. Chen"),
        ("sato_ml", "Sato ML"),
        ("kaplan_k", "K. Kaplan"),
        ("rivera_a", "A. Rivera"),
    ]
    for _login, _display in _architect_names:
        _arch = await create_live_auth_user(
            conn,
            api_url=api_url,
            anon_key=anon_key,
            service_role_key=service_role_key,
            email=f"{_login}-{suffix}@example.com",
            password=f"ArchPass!{_login}{suffix}",
            login=f"{_login}_{suffix}",
            display_name=_display,
            identity_tier="contributor",
        )
        # Give each architect a few winning submissions across the settled bounties
        _settled_ids = [settled_bounty_id]
        for _t, _e, _s, _ti in _extra_bounties:
            if _s == "settled":
                # Find the bounty_id we just inserted (use title match)
                _found = await conn.fetchval(
                    "SELECT bounty_id FROM public.bounties WHERE title = $1 LIMIT 1", _t
                )
                if _found:
                    _settled_ids.append(str(_found))

        for _sbid in _settled_ids:
            _sid = str(uuid4())
            _earn = round(50 + hash(f"{_login}{_sbid}") % 3000, 2)
            await conn.execute(
                """
                INSERT INTO public.submissions (
                    submission_id, bounty_id, architect_id, cdg_hash,
                    atom_versions, receipt_s3, receipt_json,
                    claimed_metric_name, claimed_metric_value,
                    verified_metric_value, verification_status, is_winner
                )
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4,
                        $5::jsonb, '', '{"ok":true}'::jsonb,
                        'accuracy', 0.95, 0.94, 'blind_verified', TRUE)
                ON CONFLICT DO NOTHING
                """,
                _sid,
                _sbid,
                _arch.user_id,
                f"cdg-{_login}-{_sbid[:8]}",
                f'{{"{atom_fqdn}": "{atom_content_hash}"}}',
            )
            await conn.execute(
                """
                INSERT INTO public.settlement_payouts (
                    bounty_id, recipient_id, role, amount, stripe_transfer_id, atom_fqdn, cdg_hash
                )
                VALUES ($1::uuid, $2, 'architect', $3, $4, $5, $6)
                ON CONFLICT DO NOTHING
                """,
                _sbid,
                _arch.user_id,
                _earn,
                f"tr_{_login}_{_sbid[:8]}",
                atom_fqdn,
                f"cdg-{_login}-{_sbid[:8]}",
            )

    publishable = await conn.fetchval("SELECT public.atom_is_publishable($1::uuid)", atom_id)
    if not publishable:
        raise AssertionError(f"Seed atom {atom_id} did not become publishable")
    await conn.execute(
        "UPDATE public.atoms SET is_publishable = public.atom_is_publishable($1::uuid) WHERE atom_id = $1::uuid",
        atom_id,
    )
    await conn.execute("REFRESH MATERIALIZED VIEW public.atom_audit_latest")
    await conn.execute("REFRESH MATERIALIZED VIEW public.catalog_atoms_index")
    await conn.execute("REFRESH MATERIALIZED VIEW public.reputation_leaderboard")

    return RuntimeSeed(
        suffix=suffix,
        owner=owner,
        architect=architect,
        reviewer=reviewer,
        organization_id=organization_id,
        repo_id=repo_id,
        atom_id=atom_id,
        atom_version_id=atom_version_id,
        atom_fqdn=atom_fqdn,
        atom_content_hash=atom_content_hash,
        comparison_atom_id=comparison_atom_id,
        comparison_version_id=comparison_version_id,
        comparison_fqdn=comparison_fqdn,
        comparison_content_hash=comparison_content_hash,
        search_term=search_term,
        benchmark_suite_id=benchmark_suite_id,
        settled_bounty_id=settled_bounty_id,
        settled_submission_id=settled_submission_id,
    )


async def public_tables_with_zero_rows(conn: Any) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    empty: list[str] = []
    for row in rows:
        table = row["table_name"]
        count = await conn.fetchval(f'SELECT COUNT(*) FROM public."{table}"')
        if int(count or 0) == 0:
            empty.append(table)
    return empty


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _append_prefer(existing: str | None, fragment: str) -> str:
    if not existing:
        return fragment
    return f"{existing},{fragment}"


def _content_range_count(headers: httpx.Headers) -> int | None:
    content_range = headers.get("content-range", "")
    if "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[-1]
    if not total.isdigit():
        return None
    return int(total)
