from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260430020000_physics_symbolic_operational_publication_audit.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_operational_publication_audit_migration_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_symbolic_loader_run_audit" in text
    assert "CREATE OR REPLACE VIEW public.physics_symbolic_publication_readiness" in text
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_symbolic_operational_publication_audit()"
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text


def test_loader_run_audit_exposes_operational_source_and_progress_fields() -> None:
    text = _migration_text()

    for required_fragment in (
        "public.physics_ingest_snapshots",
        "public.physics_equation_candidates",
        "source_system",
        "source_version",
        "source_uri",
        "retrieved_at",
        "adapter_name",
        "adapter_version",
        "license_expression",
        "payload_sha256",
        "candidate_count",
        "parse_failed_candidate_count",
        "blocked_candidate_count",
        "published_candidate_count",
        "dimension_ready_expression_count",
        "operational_status",
        "'needs_triage'",
        "'fully_published'",
    ):
        assert required_fragment in text


def test_publication_readiness_surfaces_blockers_and_materialization_mismatch() -> None:
    text = _migration_text()

    for required_fragment in (
        "public.physics_symbolic_publication_readiness",
        "public.artifact_symbolic_expressions",
        "public.artifact_symbolic_variables",
        "public.artifact_validity_bounds",
        "public.artifact_relationships",
        "public.artifact_is_publishable",
        "materialized_is_publishable",
        "computed_is_publishable",
        "readiness_blockers",
        "'artifact_not_approved'",
        "'expression_not_reviewed'",
        "'expression_not_validated'",
        "'missing_dimensional_hash'",
        "'missing_symbolic_variables'",
        "'missing_variable_dimensions'",
        "'unknown_dimension_source'",
        "'missing_dejargonized_description'",
        "'missing_reference'",
        "'missing_publication_audit_rollup'",
        "'computed_not_publishable'",
        "'publishable_materialization_mismatch'",
        "'publication_ready'",
    ):
        assert required_fragment in text


def test_operational_publication_rpc_returns_audit_arrays_and_summary_counts() -> None:
    text = _migration_text()

    for required_fragment in (
        "'loader_runs'",
        "'publication_readiness'",
        "'summary'",
        "'loader_run_count'",
        "'needs_triage_loader_run_count'",
        "'symbolic_expression_count'",
        "'publication_ready_expression_count'",
        "'publishable_materialization_mismatch_count'",
        "GRANT SELECT ON public.physics_symbolic_loader_run_audit TO authenticated",
        "GRANT SELECT ON public.physics_symbolic_publication_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_symbolic_operational_publication_audit() TO authenticated",
    ):
        assert required_fragment in text
