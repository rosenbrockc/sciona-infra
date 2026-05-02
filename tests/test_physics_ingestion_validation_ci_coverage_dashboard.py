from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501110000_physics_ingestion_validation_ci_coverage_dashboard.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_validation_ci_coverage_dashboard_rollup_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert (
        "CREATE OR REPLACE VIEW public.physics_ingestion_validation_ci_readiness"
        in text
    )
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_ingestion_validation_ci_readiness("
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


def test_validation_ci_coverage_dashboard_runs_after_coverage_dashboard() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260501090000_physics_ingestion_validation_ci_source_adapter_execution_validation.sql",
        "20260501100000_physics_ingestion_coverage_dashboard.sql",
        "20260501110000_physics_ingestion_validation_ci_coverage_dashboard.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_validation_ci_coverage_dashboard_family_rolls_up_counts_and_blockers() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "FROM public.physics_ingestion_coverage_dashboard",
        "coverage_dashboard_rollup AS ( SELECT COUNT(*) AS row_count",
        "COUNT(*) FILTER (WHERE coverage_ready) AS ready_count",
        "COUNT(*) FILTER (WHERE NOT coverage_ready) AS blocked_count",
        "COALESCE(SUM(snapshot_count), 0) AS snapshot_count",
        "COALESCE(SUM(discovered_candidate_count), 0) AS discovered_candidate_count",
        "COALESCE(SUM(parsed_candidate_count), 0) AS parsed_candidate_count",
        "COALESCE(SUM(dimensioned_candidate_count), 0) AS dimensioned_candidate_count",
        "COALESCE(SUM(symbolically_validated_candidate_count), 0) AS symbolically_validated_candidate_count",
        "COALESCE(SUM(reviewed_candidate_count), 0) AS reviewed_candidate_count",
        "COALESCE(SUM(published_candidate_count), 0) AS published_candidate_count",
        "COALESCE(SUM(symbolic_expression_count), 0) AS symbolic_expression_count",
        "COALESCE(SUM(blocker_count), 0) AS blocker_count",
        "SELECT UNNEST(row_data.blockers) AS blocker FROM public.physics_ingestion_coverage_dashboard row_data",
        "'no_physics_ingestion_coverage_dashboard_data'",
        "'physics_ingestion_coverage_dashboard'::TEXT AS readiness_family",
        "'discovered_candidate_count', cdr.discovered_candidate_count",
        "'parsed_candidate_count', cdr.parsed_candidate_count",
        "'dimensioned_candidate_count', cdr.dimensioned_candidate_count",
        "'symbolically_validated_candidate_count', cdr.symbolically_validated_candidate_count",
        "'reviewed_candidate_count', cdr.reviewed_candidate_count",
        "'published_candidate_count', cdr.published_candidate_count",
    ):
        assert required_fragment in text


def test_validation_ci_coverage_dashboard_preserves_rpc_contract_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_readiness_family TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_ready_only OR row_data.validation_ci_ready",
        "ORDER BY row_data.validation_ci_ready DESC, row_data.blocker_count, row_data.readiness_family",
        "'readiness_family_count'",
        "'validation_ci_ready_family_count'",
        "'blocked_family_count'",
        "'validation_ci_ready'",
        "COALESCE(BOOL_AND(validation_ci_ready), FALSE)",
        "GRANT SELECT ON public.physics_ingestion_validation_ci_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_ingestion_validation_ci_readiness(TEXT, TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
