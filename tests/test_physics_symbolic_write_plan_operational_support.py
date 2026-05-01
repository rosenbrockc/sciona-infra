from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260501000000_physics_symbolic_write_plan_operational_support.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


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
