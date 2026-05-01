from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_bulk_backfill_coverage_migration_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert (
        "CREATE OR REPLACE VIEW public.physics_symbolic_bulk_backfill_source_family_coverage"
        in text
    )
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_symbolic_bulk_backfill_normalization_coverage()"
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text


def test_bulk_backfill_coverage_runs_after_existing_physics_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260430000000_physics_symbolic_ingestion_wave0.sql",
        "20260430010000_physics_symbolic_ingestion_wave4_dashboard_coverage.sql",
        "20260430020000_physics_symbolic_operational_publication_audit.sql",
        "20260501000000_physics_symbolic_write_plan_operational_support.sql",
        "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_source_family_rollup_exposes_phase7_bulk_progress_counts() -> None:
    text = _migration_text()

    for required_fragment in (
        "source_system",
        "source_version",
        "source_family",
        "public.physics_ingest_snapshots",
        "public.physics_equation_candidates",
        "public.artifact_symbolic_expressions",
        "public.artifact_symbolic_variables",
        "snapshot_count",
        "discovered_candidate_count",
        "raw_imported_candidate_count",
        "parsed_candidate_count",
        "dimensioned_candidate_count",
        "reviewed_candidate_count",
        "published_candidate_count",
        "failed_or_blocked_candidate_count",
    ):
        assert required_fragment in text


def test_source_family_is_derived_without_schema_churn() -> None:
    text = _compact_sql(_migration_text())

    assert (
        "COALESCE( NULLIF(c.source_payload->>'source_family', ''), "
        "NULLIF(c.source_payload->>'family', ''), "
        "NULLIF(s.payload->>'source_family', ''), "
        "NULLIF(s.payload->>'family', ''), s.source_system ) AS source_family"
        in text
    )


def test_normalization_and_replay_readiness_counts_are_exposed() -> None:
    text = _migration_text()

    for required_fragment in (
        "raw_imported_expression_count",
        "parsed_expression_count",
        "normalized_expression_count",
        "normalization_failed_or_blocked_expression_count",
        "reviewed_expression_count",
        "validation_passed_expression_count",
        "dimension_ready_expression_count",
        "replay_ready_expression_count",
        "parse_status = 'normalized'",
        "parse_status IN ('parse_failed', 'blocked')",
        "source_expression_id <> ''",
        "canonical_expr_hash <> ''",
        "topology_hash <> ''",
        "dimensional_hash <> ''",
    ):
        assert required_fragment in text


def test_qudt_dimension_source_distinctions_are_counted_separately() -> None:
    text = _migration_text()

    for required_fragment in (
        "dimension_source = 'qudt'",
        "dimension_source = 'source'",
        "dimension_source = 'manual'",
        "dimension_source = 'inferred'",
        "dimension_source IN ('', 'unknown')",
        "qudt_dimension_variable_count",
        "source_dimension_variable_count",
        "manual_dimension_variable_count",
        "inferred_dimension_variable_count",
        "unknown_dimension_variable_count",
        "missing_dimension_signature_variable_count",
    ):
        assert required_fragment in text


def test_bulk_backfill_rpc_returns_rows_summary_and_grants() -> None:
    text = _migration_text()

    for required_fragment in (
        "'source_family_coverage'",
        "'summary'",
        "'source_family_count'",
        "'discovered_candidate_count'",
        "'normalized_expression_count'",
        "'qudt_dimension_variable_count'",
        "'source_dimension_variable_count'",
        "'replay_ready_expression_count'",
        "ORDER BY row_data.source_system, row_data.source_version, row_data.source_family",
        "GRANT SELECT ON public.physics_symbolic_bulk_backfill_source_family_coverage TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_symbolic_bulk_backfill_normalization_coverage() TO authenticated",
    ):
        assert required_fragment in text
