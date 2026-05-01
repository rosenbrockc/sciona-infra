from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501000000_physics_symbolic_write_plan_operational_support.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_write_plan_support_migration_is_additive_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_relationships_pdg_source_edge" in text
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_validity_bounds_expression_evidence"
        in text
    )
    assert "CREATE OR REPLACE VIEW public.physics_symbolic_write_plan_source_ids" in text
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_symbolic_write_plan_source_id_map("
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text


def test_write_plan_support_runs_after_physics_schema_and_readiness_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260430000000_physics_symbolic_ingestion_wave0.sql",
        "20260430010000_physics_symbolic_ingestion_wave4_dashboard_coverage.sql",
        "20260430020000_physics_symbolic_operational_publication_audit.sql",
        "20260501000000_physics_symbolic_write_plan_operational_support.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_write_plan_support_adds_conflict_targets_for_repeatable_loads() -> None:
    text = _migration_text()

    for required_fragment in (
        "ON public.artifact_relationships",
        "source_kind",
        "relationship_kind",
        "source_expression_id",
        "target_expression_id",
        "source_node_id",
        "target_node_id",
        "inference_rule_id",
        "WHERE source_kind = 'physics_derivation_graph'",
        "source_expression_id IS NOT NULL",
        "target_expression_id IS NOT NULL",
        "source_node_id <> ''",
        "target_node_id <> ''",
        "inference_rule_id <> ''",
        "ON public.artifact_validity_bounds",
        "expression_id",
        "scope",
        "bound_kind",
        "variable_name",
        "evidence_ref_key",
        "evidence_ref_key <> ''",
    ):
        assert required_fragment in text


def test_write_plan_relationship_conflict_target_matches_replay_upsert_order() -> None:
    text = _compact_sql(_migration_text())

    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_relationships_pdg_source_edge "
        "ON public.artifact_relationships ( source_kind, relationship_kind, "
        "source_expression_id, target_expression_id, source_node_id, target_node_id, "
        "inference_rule_id ) WHERE source_kind = 'physics_derivation_graph' "
        "AND source_expression_id IS NOT NULL AND target_expression_id IS NOT NULL "
        "AND source_node_id <> '' AND target_node_id <> '' AND inference_rule_id <> ''"
        in text
    )


def test_write_plan_source_id_map_exposes_deterministic_loader_keys() -> None:
    text = _migration_text()

    for required_fragment in (
        "public.physics_ingest_snapshots",
        "public.physics_equation_candidates",
        "public.artifact_symbolic_expressions",
        "public.artifacts",
        "public.artifact_versions",
        "source_system",
        "source_version",
        "source_uri",
        "snapshot_id",
        "payload_sha256",
        "candidate_id",
        "source_candidate_id",
        "candidate_raw_formula",
        "expression_id",
        "source_expression_id",
        "artifact_id",
        "fqdn",
        "version_id",
        "semver",
        "request_source_system",
        "request_source_version",
    ):
        assert required_fragment in text


def test_write_plan_source_id_map_orders_replay_lookup_rows_deterministically() -> None:
    text = _compact_sql(_migration_text())

    assert (
        "ORDER BY row_data.source_system, row_data.source_version, row_data.source_uri, "
        "row_data.source_candidate_id, row_data.source_expression_id, row_data.fqdn, "
        "row_data.semver"
        in text
    )


def test_write_plan_source_id_surfaces_are_granted_to_authenticated() -> None:
    text = _migration_text()

    assert (
        "GRANT SELECT ON public.physics_symbolic_write_plan_source_ids TO authenticated"
        in text
    )
    assert (
        "GRANT EXECUTE ON FUNCTION public.physics_symbolic_write_plan_source_id_map(TEXT, TEXT) TO authenticated"
        in text
    )
