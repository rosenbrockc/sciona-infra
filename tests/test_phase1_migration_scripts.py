from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import migrate_phase1_organizations as phase1_orgs
import migrate_phase1_tables as phase1_tables
import migrate_phase1_users as phase1_users
import phase1_validate
import phase1_common


def test_build_auth_user_payload_uses_noreply_email_and_metadata() -> None:
    payload = phase1_users.build_auth_user_payload(
        {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "github_id": 42,
            "github_login": "octocat",
            "display_name": "The Octocat",
            "avatar_url": "https://example.com/avatar.png",
            "email": "",
        },
        noreply_domain="github-noreply.example",
    )

    assert payload["id"] == "00000000-0000-0000-0000-000000000001"
    assert payload["email"] == "octocat@github-noreply.example"
    assert payload["user_metadata"]["provider_id"] == "42"
    assert payload["user_metadata"]["user_name"] == "octocat"


def test_is_idempotent_auth_response_matches_expected_duplicates() -> None:
    assert phase1_users.is_idempotent_auth_response(422, "already been registered")
    assert phase1_users.is_idempotent_auth_response(409, "duplicate key value")
    assert not phase1_users.is_idempotent_auth_response(500, "duplicate key value")


def test_chunked_and_build_batch_params_helpers() -> None:
    chunks = list(phase1_common.chunked([1, 2, 3, 4, 5], 2))
    params = phase1_common.build_batch_params(
        [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
        ("a", "b"),
    )

    assert chunks == [[1, 2], [3, 4], [5]]
    assert params == [(1, 2), (3, 4)]


def test_load_organization_seeds_from_file(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "orgs.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "Sciona Foundation",
                    "entitlement_tier": "internal",
                    "email_domains": ["Sciona.Org", "@staff.sciona.org", "Sciona.Org"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("PHASE1_ORGANIZATIONS_JSON", raising=False)
    monkeypatch.setenv("PHASE1_ORGANIZATIONS_FILE", str(config_path))

    seeds = phase1_common.load_organization_seeds_from_env()

    assert seeds == [
        phase1_common.OrganizationSeed(
            name="Sciona Foundation",
            entitlement_tier="internal",
            email_domains=("sciona.org", "staff.sciona.org"),
        )
    ]


def test_phase1_org_loader_defaults_to_noop_without_config(monkeypatch) -> None:
    monkeypatch.delenv("PHASE1_ORGANIZATIONS_JSON", raising=False)
    monkeypatch.delenv("PHASE1_ORGANIZATIONS_FILE", raising=False)

    assert phase1_common.load_organization_seeds_from_env() == []


def test_table_specs_cover_phase1_scope() -> None:
    expected = {
        "atoms",
        "atom_versions",
        "atom_authors",
        "hyperparams",
        "atom_benchmarks",
        "bounties",
        "submissions",
        "payouts",
        "verification_budgets",
        "verification_runs",
        "bounty_best_scores",
        "principal_targets",
        "execution_receipts",
        "dataset_splits",
        "settlement_payouts",
        "benchmark_suites",
        "benchmark_votes",
        "fuzz_results",
        "behavioral_equivalence_flags",
        "discipline_repos",
    }

    assert set(phase1_tables.spec_map()) == expected
    assert list(phase1_tables.MIGRATION_ORDER) == [spec.table for spec in phase1_tables.TABLE_SPECS]


def test_resolve_table_specs_rejects_unknown_names() -> None:
    try:
        phase1_tables.resolve_table_specs(["atoms", "missing_table"])
    except ValueError as exc:
        assert "missing_table" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown table")


def test_organization_migration_config_type() -> None:
    config = phase1_orgs.OrganizationMigrationConfig(dry_run=True)
    assert config.dry_run is True


def test_phase1_validation_scope_matches_phase1_tables_plus_users() -> None:
    validation_tables = {table for table, _columns in phase1_validate.VALIDATION_TABLES}
    migration_tables = set(phase1_tables.spec_map())

    assert validation_tables == migration_tables | {"users"}
