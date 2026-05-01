from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501090000_physics_ingestion_validation_ci_source_adapter_execution_validation.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_validation_ci_readiness_is_read_only_and_idempotent() -> None:
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


def test_validation_ci_readiness_runs_after_composed_readiness_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260430020000_physics_symbolic_operational_publication_audit.sql",
        "20260501030000_physics_cdg_artifact_envelope_publication.sql",
        "20260501040000_physics_backfill_review_publication_observability.sql",
        "20260501050000_physics_source_retrieval_backfill_readiness.sql",
        "20260501060000_physics_cdg_graph_readiness.sql",
        "20260501070000_physics_ingestion_validation_ci_readiness.sql",
        "20260501080000_physics_source_adapter_execution_validation_readiness.sql",
        "20260501090000_physics_ingestion_validation_ci_source_adapter_execution_validation.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_validation_ci_readiness_composes_existing_readiness_views() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.physics_source_retrieval_backfill_readiness",
        "FROM public.physics_cdg_graph_readiness",
        "FROM public.physics_symbolic_publication_readiness",
        "FROM public.physics_backfill_review_publication_status_rows",
        "FROM public.physics_cdg_artifact_envelope_publication",
        "FROM public.physics_source_adapter_execution_validation_readiness",
        "'source_retrieval_backfill'::TEXT AS readiness_family",
        "'cdg_graph'::TEXT AS readiness_family",
        "'symbolic_publication'::TEXT AS readiness_family",
        "'backfill_publication_observability'::TEXT AS readiness_family",
        "'cdg_publication'::TEXT AS readiness_family",
        "'source_adapter_execution_validation'::TEXT AS readiness_family",
    ):
        assert required_fragment in text


def test_validation_ci_readiness_exposes_dashboard_counts_status_and_detail() -> None:
    text = _migration_text()

    for required_fragment in (
        "readiness_family",
        "row_count",
        "ready_count",
        "blocked_count",
        "blockers",
        "blocker_count",
        "validation_ci_ready",
        "readiness_status",
        "readiness_detail",
        "jsonb_build_object(",
        "'source_row_count'",
        "'missing_edge_endpoint_count'",
        "'expression_not_validated_count'",
        "'review_patch_pending_row_count'",
        "'publication_ready_row_count'",
        "'validation_passed_expression_count'",
        "'snapshot_count'",
        "'candidate_count'",
        "'symbolic_expression_count'",
        "'blocker_count'",
    ):
        assert required_fragment in text


def test_validation_ci_readiness_has_explicit_no_data_and_blocker_logic() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "'no_source_retrieval_backfill_readiness_data'",
        "'no_cdg_graph_readiness_data'",
        "'no_symbolic_publication_readiness_data'",
        "'no_backfill_publication_observability_data'",
        "'no_cdg_publication_readiness_data'",
        "'no_source_adapter_execution_validation_readiness_data'",
        "CASE WHEN row_count = 0 THEN 'no_data'",
        "row_count > 0 AND ready_count = row_count AND blocked_count = 0 AND CARDINALITY(blockers) = 0",
        "COUNT(*) FILTER (WHERE backfill_ready) AS ready_count",
        "COUNT(*) FILTER (WHERE graph_ready) AS ready_count",
        "COUNT(*) FILTER (WHERE readiness_status = 'publication_ready') AS ready_count",
        "COUNT(*) FILTER ( WHERE blocked_row_count = 0 AND review_patch_pending_row_count = 0 ) AS ready_count",
        "COUNT(*) FILTER (WHERE publication_ready) AS ready_count",
        "COUNT(*) FILTER (WHERE source_execution_validation_ready) AS ready_count",
    ):
        assert required_fragment in text


def test_source_adapter_execution_validation_family_rolls_up_counts_and_blockers() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "source_adapter_execution_validation_rollup AS ( SELECT COUNT(*) AS row_count",
        "COUNT(*) FILTER (WHERE NOT source_execution_validation_ready) AS blocked_count",
        "COALESCE(SUM(snapshot_count), 0) AS snapshot_count",
        "COALESCE(SUM(candidate_count), 0) AS candidate_count",
        "COALESCE(SUM(symbolic_expression_count), 0) AS symbolic_expression_count",
        "COALESCE(SUM(validation_passed_expression_count), 0) AS validation_passed_expression_count",
        "COALESCE(SUM(blocker_count), 0) AS blocker_count",
        "SELECT UNNEST(row_data.blockers) AS blocker FROM public.physics_source_adapter_execution_validation_readiness row_data",
        "'source_adapter_execution_validation'::TEXT AS readiness_family",
        "'snapshot_count', saavr.snapshot_count",
        "'candidate_count', saavr.candidate_count",
        "'symbolic_expression_count', saavr.symbolic_expression_count",
        "'validation_passed_expression_count', saavr.validation_passed_expression_count",
        "'blocker_count', saavr.blocker_count",
    ):
        assert required_fragment in text


def test_backfill_publication_blockers_include_conflict_replay_review_and_blocked_rows() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "CASE WHEN row_data.review_patch_pending_row_count > 0 THEN 'review_patch_pending' END",
        "CASE WHEN row_data.blocked_row_count > 0 THEN row_data.status_family || '_blocked' END",
        "CASE WHEN row_data.conflict_keyed_row_count <> row_data.source_row_count THEN row_data.status_family || '_conflict_key_gap' END",
        "CASE WHEN row_data.normalized_replay_ready_row_count <> row_data.normalized_row_count THEN row_data.status_family || '_replay_gap' END",
    ):
        assert required_fragment in text


def test_validation_ci_readiness_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_readiness_family TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "'readiness_families'",
        "'summary'",
        "request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_ready_only OR row_data.validation_ci_ready",
        "ORDER BY row_data.validation_ci_ready DESC, row_data.blocker_count, row_data.readiness_family",
        "'readiness_family_count'",
        "'validation_ci_ready_family_count'",
        "'blocked_family_count'",
        "'row_count'",
        "'ready_count'",
        "'blocked_count'",
        "'blocker_count'",
        "'validation_ci_ready'",
        "COALESCE(BOOL_AND(validation_ci_ready), FALSE)",
        "GRANT SELECT ON public.physics_ingestion_validation_ci_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_ingestion_validation_ci_readiness(TEXT, TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
