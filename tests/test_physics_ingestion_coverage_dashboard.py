from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR / "20260501100000_physics_ingestion_coverage_dashboard.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_coverage_dashboard_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_ingestion_coverage_dashboard" in text
    assert "CREATE OR REPLACE FUNCTION public.physics_ingestion_coverage_dashboard(" in text
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text
    assert "INSERT INTO" not in text
    assert "UPDATE " not in text
    assert "DELETE FROM" not in text


def test_coverage_dashboard_runs_after_phase6_validation_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql",
        "20260501080000_physics_source_adapter_execution_validation_readiness.sql",
        "20260501090000_physics_ingestion_validation_ci_source_adapter_execution_validation.sql",
        "20260501100000_physics_ingestion_coverage_dashboard.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_coverage_dashboard_uses_persisted_source_state_only() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.physics_ingest_snapshots s",
        "LEFT JOIN public.physics_equation_candidates c",
        "LEFT JOIN public.artifact_symbolic_expressions se",
        "LEFT JOIN public.physics_symbolic_publication_readiness pr",
        "s.source_system",
        "s.source_version",
        "s.payload",
        "c.source_payload",
        "c.mechanism_tags[1]",
        "c.candidate_status",
        "se.parse_status",
        "se.review_status",
        "se.validation_status",
        "se.dimensional_hash",
    ):
        assert required_fragment in text

    assert "expected_source" not in text
    assert "required_source" not in text
    assert "coverage_target" not in text


def test_source_and_physics_family_are_derived_from_persisted_payloads() -> None:
    text = _compact_sql(_migration_text())

    assert (
        "COALESCE( NULLIF(c.source_payload->>'source_family', ''), "
        "NULLIF(c.source_payload->>'family', ''), "
        "NULLIF(s.payload->>'source_family', ''), "
        "NULLIF(s.payload->>'family', ''), s.source_system ) AS source_family"
        in text
    )
    assert (
        "COALESCE( NULLIF(c.source_payload->>'physics_family', ''), "
        "NULLIF(c.source_payload->>'physics_domain', ''), "
        "NULLIF(c.source_payload->>'discipline', ''), "
        "NULLIF(s.payload->>'physics_family', ''), "
        "NULLIF(s.payload->>'physics_domain', ''), "
        "NULLIF(s.payload->>'discipline', ''), "
        "NULLIF(c.mechanism_tags[1], ''), s.source_system ) AS physics_family"
        in text
    )


def test_coverage_dashboard_exposes_phase7_counts_status_and_blockers() -> None:
    text = _migration_text()

    for required_fragment in (
        "source_system",
        "source_family",
        "physics_family",
        "snapshot_count",
        "discovered_candidate_count",
        "raw_candidate_count",
        "parsed_candidate_count",
        "dimensioned_candidate_count",
        "symbolically_validated_candidate_count",
        "reviewed_candidate_count",
        "published_candidate_count",
        "failed_or_blocked_candidate_count",
        "source_candidate_id_count",
        "symbolic_expression_count",
        "parsed_expression_count",
        "dimensioned_expression_count",
        "symbolically_validated_expression_count",
        "reviewed_expression_count",
        "published_expression_count",
        "publication_ready_expression_count",
        "blockers",
        "blocker_count",
        "coverage_ready",
        "coverage_status",
    ):
        assert required_fragment in text


def test_coverage_dashboard_count_semantics_are_explicit() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "WHERE candidate_status = 'raw_imported'",
        "WHERE candidate_status IN ( 'parsed', 'dimension_resolved', 'symbolically_validated', 'source_verified', 'human_reviewed', 'published' )",
        "WHERE candidate_status IN ( 'dimension_resolved', 'symbolically_validated', 'source_verified', 'human_reviewed', 'published' )",
        "WHERE candidate_status IN ( 'symbolically_validated', 'source_verified', 'human_reviewed', 'published' )",
        "WHERE candidate_status IN ('human_reviewed', 'published')",
        "WHERE candidate_status = 'published'",
        "WHERE candidate_status IN ('parse_failed', 'blocked')",
        "WHERE parse_status IN ('parsed', 'normalized')",
        "WHERE dimensional_hash <> ''",
        "WHERE validation_status = 'passed'",
        "WHERE review_status IN ('automated_pass', 'human_reviewed')",
        "WHERE publication_readiness_status = 'publication_ready'",
    ):
        assert required_fragment in text


def test_coverage_dashboard_has_no_data_and_blocker_logic() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "CASE WHEN discovered_candidate_count = 0 THEN 'no_discovered_candidates' END",
        "CASE WHEN discovered_candidate_count > 0 AND parsed_candidate_count <> discovered_candidate_count THEN 'parse_coverage_gap' END",
        "CASE WHEN discovered_candidate_count > 0 AND dimensioned_candidate_count <> discovered_candidate_count THEN 'dimension_coverage_gap' END",
        "CASE WHEN discovered_candidate_count > 0 AND symbolically_validated_candidate_count <> discovered_candidate_count THEN 'symbolic_validation_gap' END",
        "CASE WHEN discovered_candidate_count > 0 AND reviewed_candidate_count <> discovered_candidate_count THEN 'review_coverage_gap' END",
        "CASE WHEN discovered_candidate_count > 0 AND published_candidate_count <> discovered_candidate_count THEN 'publication_coverage_gap' END",
        "CASE WHEN failed_or_blocked_candidate_count > 0 THEN 'failed_or_blocked_candidates' END",
        "CASE WHEN discovered_candidate_count > 0 AND symbolic_expression_count = 0 THEN 'no_symbolic_expressions' END",
        "CASE WHEN discovered_candidate_count = 0 THEN 'no_data'",
        "WHEN failed_or_blocked_candidate_count > 0 THEN 'blocked'",
        "WHEN published_candidate_count = discovered_candidate_count AND CARDINALITY(blockers) = 0 THEN 'published'",
        "ELSE 'discovered_not_parsed'",
    ):
        assert required_fragment in text


def test_coverage_dashboard_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_source_system TEXT DEFAULT NULL",
        "request_source_family TEXT DEFAULT NULL",
        "request_physics_family TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "'coverage_families'",
        "'summary'",
        "request_source_system IS NULL OR row_data.source_system = request_source_system",
        "request_source_family IS NULL OR row_data.source_family = request_source_family",
        "request_physics_family IS NULL OR row_data.physics_family = request_physics_family",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_ready_only OR row_data.coverage_ready",
        "ORDER BY row_data.coverage_ready DESC, row_data.blocker_count, row_data.source_system, row_data.source_family, row_data.physics_family",
        "'coverage_family_count'",
        "'coverage_ready_family_count'",
        "'blocked_family_count'",
        "'snapshot_count'",
        "'discovered_candidate_count'",
        "'parsed_candidate_count'",
        "'dimensioned_candidate_count'",
        "'symbolically_validated_candidate_count'",
        "'reviewed_candidate_count'",
        "'published_candidate_count'",
        "'symbolic_expression_count'",
        "'blocker_count'",
        "GRANT SELECT ON public.physics_ingestion_coverage_dashboard TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_ingestion_coverage_dashboard(TEXT, TEXT, TEXT, TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
