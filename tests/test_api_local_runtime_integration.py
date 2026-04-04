from __future__ import annotations

import base64
import importlib
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from runtime_seed_helpers import (
    LiveSupabaseClient,
    connect,
    public_tables_with_zero_rows,
    seed_runtime_dataset,
)


@pytest_asyncio.fixture
async def runtime_seed(supabase_local_env: dict[str, str]):
    conn = await connect(supabase_local_env["db_url"])
    try:
        yield await seed_runtime_dataset(
            conn,
            api_url=supabase_local_env["api_url"],
            anon_key=supabase_local_env["anon_key"],
            service_role_key=supabase_local_env["service_role_key"],
        )
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def live_api_client(monkeypatch: pytest.MonkeyPatch, supabase_local_env: dict[str, str]):
    monkeypatch.setenv("AUTHENTIK_URL", "")
    monkeypatch.setenv("OPA_URL", "http://localhost:8181")
    monkeypatch.setenv("OPA_POLICY_MODE", "strict")

    for module_name in [
        "sciona_infra.api.policy",
        "sciona_infra.api.deps",
        "sciona_infra.api.routers.auth",
        "sciona_infra.api.routers.bounty",
        "sciona_infra.api.routers.catalog",
        "sciona_infra.api.routers.dashboard",
        "sciona_infra.api.routers.registry",
        "sciona_infra.api.routers.scim",
        "sciona_infra.api.routers.verification",
        "sciona_infra.api.app",
    ]:
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("sciona_infra.api.app")
    create_app = getattr(app_module, "create_app")
    app = create_app()

    supabase = LiveSupabaseClient(
        supabase_local_env["api_url"],
        supabase_local_env["service_role_key"],
    )
    app.state.supabase = supabase
    app.state.supabase_admin = supabase

    temporal_client = None
    try:
        from temporalio.client import Client as TemporalClient

        temporal_client = await TemporalClient.connect("localhost:7233")
        app.state.temporal = temporal_client
    except Exception:
        temporal_client = None

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=30.0,
    ) as client:
        yield client



@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_runtime_seed_populates_all_public_tables(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await connect(supabase_local_env["db_url"])
    try:
        seeded = await seed_runtime_dataset(
            conn,
            api_url=supabase_local_env["api_url"],
            anon_key=supabase_local_env["anon_key"],
            service_role_key=supabase_local_env["service_role_key"],
        )
        zero_tables = await public_tables_with_zero_rows(conn)
        assert zero_tables == []

        publishable = await conn.fetchval(
            "SELECT is_publishable FROM public.atoms WHERE atom_id = $1::uuid",
            seeded.atom_id,
        )
        assert publishable is True

        catalog_row = await conn.fetchrow(
            "SELECT fqdn, overall_verdict FROM public.catalog_atoms_served WHERE fqdn = $1",
            seeded.atom_fqdn,
        )
        assert catalog_row is not None
        assert catalog_row["overall_verdict"] == "trusted"
    finally:
        await conn.close()


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_api_runtime_endpoints_use_live_services(
    live_api_client: httpx.AsyncClient,
    runtime_seed,
) -> None:
    owner_headers = {"Authorization": f"Bearer {runtime_seed.owner.token}"}
    architect_headers = {"Authorization": f"Bearer {runtime_seed.architect.token}"}

    catalog_response = await live_api_client.get(
        "/catalog/search",
        params={"q": runtime_seed.search_term, "domain_tag": "signal"},
    )
    assert catalog_response.status_code == 200, catalog_response.text
    catalog_rows = catalog_response.json()
    assert any(row["fqdn"] == runtime_seed.atom_fqdn for row in catalog_rows)

    atom_document = await live_api_client.get(f"/catalog/atom/{runtime_seed.atom_fqdn}")
    assert atom_document.status_code == 200, atom_document.text
    atom_doc_json = atom_document.json()
    assert atom_doc_json["atom"]["fqdn"] == runtime_seed.atom_fqdn
    assert atom_doc_json["audit_rollup"]["overall_verdict"] == "trusted"

    registry_list = await live_api_client.get("/atoms", params={"q": runtime_seed.search_term})
    assert registry_list.status_code == 200, registry_list.text
    registry_list_json = registry_list.json()
    assert any(item["fqdn"] == runtime_seed.atom_fqdn for item in registry_list_json["items"])

    registry_detail = await live_api_client.get(f"/atoms/{runtime_seed.atom_fqdn}")
    assert registry_detail.status_code == 200, registry_detail.text
    assert registry_detail.json()["owner_github_login"] == runtime_seed.owner.github_login

    registry_versions = await live_api_client.get(f"/atoms/{runtime_seed.atom_fqdn}/versions")
    assert registry_versions.status_code == 200, registry_versions.text
    assert registry_versions.json()[0]["content_hash"] == runtime_seed.atom_content_hash

    publish_suffix = runtime_seed.suffix + "-publish"
    publish_body = {
        "fqdn": f"pkg.api_publish_{publish_suffix}",
        "semver": "0.1.0",
        "description": "API publish smoke test",
        "domain_tags": ["signal", "testing"],
        "source_tar_b64": base64.b64encode(f"publish-{publish_suffix}".encode()).decode(),
        "fingerprint": "c" * 64,
    }
    publish_response = await live_api_client.post(
        "/atoms",
        headers=owner_headers,
        json=publish_body,
    )
    assert publish_response.status_code == 200, publish_response.text
    published = publish_response.json()
    assert published["fqdn"] == publish_body["fqdn"]
    assert published["is_new_atom"] is True

    create_response = await live_api_client.post(
        "/bounties",
        headers=owner_headers,
        json={
            "title": f"API runtime bounty {runtime_seed.suffix}",
            "escrow_amount": 125.0,
            "deadline": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "tier": "standard",
            "domain_tags": ["signal"],
            "flare_payload": {"mode": "runtime"},
            "config_yml": {"min_metric_value": 0.91},
        },
    )
    assert create_response.status_code == 200, create_response.text
    created_bounty = create_response.json()
    bounty_id = created_bounty["bounty_id"]
    assert created_bounty["status"] == "draft"

    fund_response = await live_api_client.post(
        f"/bounties/{bounty_id}/fund",
        headers=owner_headers,
    )
    assert fund_response.status_code == 200, fund_response.text
    assert fund_response.json()["status"] == "open"

    target_response = await live_api_client.post(
        f"/bounties/{bounty_id}/target",
        headers=owner_headers,
        json={"min_metric_value": 0.93},
    )
    assert target_response.status_code == 200, target_response.text
    assert target_response.json()["status"] == "open"

    submit_response = await live_api_client.post(
        f"/bounties/{bounty_id}/submit",
        headers=architect_headers,
        json={
            "cdg_hash": f"api-cdg-{runtime_seed.suffix}",
            "atom_versions": {runtime_seed.atom_fqdn: runtime_seed.atom_content_hash},
            "receipt_json": {"metric_name": "accuracy", "metric_value": 0.94},
            "claimed_metric_name": "accuracy",
            "claimed_metric_value": 0.94,
        },
    )
    assert submit_response.status_code == 200, submit_response.text
    created_submission = submit_response.json()
    assert created_submission["bounty_id"] == bounty_id

    bounty_detail = await live_api_client.get(f"/bounties/{bounty_id}")
    assert bounty_detail.status_code == 200, bounty_detail.text
    assert bounty_detail.json()["status"] == "submitted"

    bounty_list = await live_api_client.get("/bounties", params={"status": "submitted"})
    assert bounty_list.status_code == 200, bounty_list.text
    assert any(item["bounty_id"] == bounty_id for item in bounty_list.json()["items"])

    submission_status = await live_api_client.get(
        f"/submissions/{runtime_seed.settled_submission_id}/status"
    )
    assert submission_status.status_code == 200, submission_status.text
    submission_status_json = submission_status.json()
    assert submission_status_json["verification_status"] == "blind_verified"
    assert submission_status_json["runs"][0]["status"] == "completed"

    leaderboard = await live_api_client.get(
        f"/bounties/{runtime_seed.settled_bounty_id}/leaderboard"
    )
    assert leaderboard.status_code == 200, leaderboard.text
    leaderboard_json = leaderboard.json()
    assert leaderboard_json["items"][0]["submission_id"] == runtime_seed.settled_submission_id

    settlement = await live_api_client.get(
        f"/bounties/{runtime_seed.settled_bounty_id}/settlement"
    )
    assert settlement.status_code == 200, settlement.text
    settlement_json = settlement.json()
    assert settlement_json["status"] == "settled"
    assert len(settlement_json["payouts"]) == 2

    impact = await live_api_client.get(
        f"/dashboard/originator/{runtime_seed.owner.user_id}/impact"
    )
    assert impact.status_code == 200, impact.text
    impact_json = impact.json()
    assert impact_json["originator_id"] == runtime_seed.owner.user_id
    assert impact_json["atom_count"] >= 1

    benchmarks = await live_api_client.get(
        f"/dashboard/atom/{runtime_seed.atom_fqdn}/benchmarks"
    )
    assert benchmarks.status_code == 200, benchmarks.text
    benchmarks_json = benchmarks.json()
    assert benchmarks_json[0]["metric_name"] == "accuracy"

    bibtex = await live_api_client.get(
        f"/dashboard/atom/{runtime_seed.atom_fqdn}/bibtex"
    )
    assert bibtex.status_code == 200, bibtex.text
    assert runtime_seed.atom_fqdn in bibtex.json()["bibtex"]

    compute_preserved = await live_api_client.get("/dashboard/compute-preserved")
    assert compute_preserved.status_code == 200, compute_preserved.text
    compute_json = compute_preserved.json()
    assert compute_json["total_bounties_settled"] >= 1
    assert compute_json["estimated_tokens_saved"] > 0
    assert compute_json["estimated_cost_saved_usd"] > 0

    dashboard_leaderboard = await live_api_client.get("/dashboard/leaderboard")
    assert dashboard_leaderboard.status_code == 200, dashboard_leaderboard.text
    assert any(
        row["originator_id"] == runtime_seed.owner.user_id
        for row in dashboard_leaderboard.json()
    )
