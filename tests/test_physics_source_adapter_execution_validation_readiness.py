from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501080000_physics_source_adapter_execution_validation_readiness.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_source_adapter_execution_validation_readiness_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert (
        "CREATE OR REPLACE VIEW public.physics_source_adapter_execution_validation_readiness"
        in text
    )
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_source_adapter_execution_validation_observability("
        in text
    )
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


def test_source_adapter_execution_validation_runs_after_ci_readiness() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260501070000_physics_ingestion_validation_ci_readiness.sql",
        "20260501080000_physics_source_adapter_execution_validation_readiness.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_source_adapter_execution_validation_uses_only_persisted_execution_state() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.physics_ingest_snapshots s",
        "LEFT JOIN public.physics_equation_candidates c",
        "LEFT JOIN public.artifact_symbolic_expressions se",
        "s.adapter_name",
        "s.adapter_version",
        "s.source_uri",
        "s.payload_sha256",
        "c.candidate_status",
        "se.parse_status",
        "se.validation_status",
    ):
        assert required_fragment in text

    assert "expected_adapter" not in text
    assert "expected_source" not in text
    assert "required_adapter" not in text


def test_source_adapter_execution_validation_exposes_status_counts_and_blockers() -> None:
    text = _migration_text()

    for required_fragment in (
        "source_system",
        "source_version",
        "source_family",
        "adapter_name",
        "adapter_version",
        "snapshot_count",
        "adapter_metadata_ready_snapshot_count",
        "provenance_ready_snapshot_count",
        "candidate_count",
        "executed_candidate_count",
        "execution_blocked_or_pending_candidate_count",
        "symbolic_expression_count",
        "parsed_expression_count",
        "validation_passed_expression_count",
        "validation_blocked_expression_count",
        "blockers",
        "blocker_count",
        "source_execution_validation_ready",
        "readiness_status",
        "'adapter_metadata_gap'",
        "'source_provenance_gap'",
        "'empty_adapter_run'",
        "'source_execution_gap'",
        "'no_symbolic_expressions'",
        "'expression_parse_gap'",
        "'expression_validation_gap'",
    ):
        assert required_fragment in text


def test_source_family_and_ready_logic_are_explicit() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "COALESCE( NULLIF(c.source_payload->>'source_family', ''), NULLIF(c.source_payload->>'family', ''), NULLIF(s.payload->>'source_family', ''), NULLIF(s.payload->>'family', ''), s.source_system ) AS source_family",
        "WHERE candidate_status IN ( 'parsed', 'dimension_resolved', 'symbolically_validated', 'source_verified', 'human_reviewed', 'published' )",
        "WHERE candidate_status IN ('raw_imported', 'parse_failed', 'blocked')",
        "WHERE parse_status IN ('parsed', 'normalized')",
        "WHERE validation_status = 'passed'",
        "snapshot_count > 0 AND adapter_metadata_ready_snapshot_count = snapshot_count AND provenance_ready_snapshot_count = snapshot_count AND candidate_count > 0 AND executed_candidate_count = candidate_count AND execution_blocked_or_pending_candidate_count = 0 AND symbolic_expression_count > 0 AND parsed_expression_count = symbolic_expression_count AND validation_passed_expression_count = symbolic_expression_count",
        "CASE WHEN snapshot_count = 0 THEN 'no_data'",
    ):
        assert required_fragment in text


def test_source_adapter_execution_validation_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_source_family TEXT DEFAULT NULL",
        "request_adapter_name TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "'source_adapter_execution_validation'",
        "'summary'",
        "request_source_family IS NULL OR row_data.source_family = request_source_family",
        "request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_ready_only OR row_data.source_execution_validation_ready",
        "ORDER BY row_data.source_execution_validation_ready DESC, row_data.blocker_count, row_data.source_system, row_data.source_version, row_data.source_family, row_data.adapter_name, row_data.adapter_version",
        "'adapter_execution_family_count'",
        "'ready_adapter_execution_family_count'",
        "'snapshot_count'",
        "'candidate_count'",
        "'symbolic_expression_count'",
        "'validation_passed_expression_count'",
        "'blocker_count'",
        "GRANT SELECT ON public.physics_source_adapter_execution_validation_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_source_adapter_execution_validation_observability(TEXT, TEXT, TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
