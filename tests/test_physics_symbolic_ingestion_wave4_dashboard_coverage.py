from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260430010000_physics_symbolic_ingestion_wave4_dashboard_coverage.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_wave4_dashboard_migration_is_additive_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_symbolic_ingestion_source_coverage" in text
    assert "CREATE OR REPLACE VIEW public.physics_symbolic_ingestion_status_coverage" in text
    assert "CREATE OR REPLACE VIEW public.physics_symbolic_relationship_kind_coverage" in text
    assert "CREATE OR REPLACE VIEW public.physics_symbolic_pdg_cdg_readiness" in text
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_symbolic_ingestion_dashboard_coverage()"
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text


def test_wave4_source_and_status_coverage_surface_requested_dimensions() -> None:
    text = _migration_text()

    for required_fragment in (
        "source_system",
        "source_version",
        "candidate_status",
        "parse_status",
        "review_status",
        "validation_status",
        "dimension_status",
        "dimension_ready_expression_count",
        "parsed_or_later_candidate_count",
        "dimension_resolved_or_later_candidate_count",
        "validation_passed_expression_count",
        "public.physics_ingest_snapshots",
        "public.physics_equation_candidates",
        "public.artifact_symbolic_expressions",
        "public.artifact_symbolic_variables",
    ):
        assert required_fragment in text

    assert "sv.variable_role <> 'intermediate'" in text
    assert "sv.dim_signature = ''" in text
    assert "sv.dimension_source IN ('', 'unknown')" in text


def test_wave4_relationship_and_pdg_cdg_readiness_are_exposed() -> None:
    text = _migration_text()

    for required_fragment in (
        "relationship_kind",
        "source_kind",
        "verified",
        "expression_endpoint_bound_count",
        "pdg_relationship_ready_count",
        "cdg_candidate_ready_count",
        "missing_expression_endpoint_count",
        "source_kind = 'physics_derivation_graph'",
        "source_node_id <> ''",
        "target_node_id <> ''",
        "inference_rule_id <> ''",
        "evidence_json->>'operation_kind'",
        "algebraic_rearrangement_of",
        "limit_case_of",
        "approximation_of",
    ):
        assert required_fragment in text


def test_wave4_dashboard_rpc_returns_all_sql_surfaces() -> None:
    text = _migration_text()

    for json_key in (
        "'source_coverage'",
        "'status_coverage'",
        "'relationship_kind_coverage'",
        "'pdg_cdg_readiness'",
    ):
        assert json_key in text

    for grant in (
        "GRANT SELECT ON public.physics_symbolic_ingestion_source_coverage TO authenticated",
        "GRANT SELECT ON public.physics_symbolic_ingestion_status_coverage TO authenticated",
        "GRANT SELECT ON public.physics_symbolic_relationship_kind_coverage TO authenticated",
        "GRANT SELECT ON public.physics_symbolic_pdg_cdg_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_symbolic_ingestion_dashboard_coverage() TO authenticated",
    ):
        assert grant in text
