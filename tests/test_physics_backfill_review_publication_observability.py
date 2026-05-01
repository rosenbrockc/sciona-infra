from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501040000_physics_backfill_review_publication_observability.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_backfill_review_publication_observability_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert (
        "CREATE OR REPLACE VIEW public.physics_backfill_review_publication_status_rows"
        in text
    )
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_backfill_review_publication_observability("
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text


def test_backfill_review_publication_observability_runs_after_composed_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260430000000_physics_symbolic_ingestion_wave0.sql",
        "20260501000000_physics_symbolic_write_plan_operational_support.sql",
        "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql",
        "20260501020000_physics_symbolic_retrieval_support.sql",
        "20260501030000_physics_cdg_artifact_envelope_publication.sql",
        "20260501040000_physics_backfill_review_publication_observability.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_status_rows_compose_existing_retrieval_and_cdg_envelope_views() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.physics_symbolic_retrieval_rows",
        "JOIN public.physics_cdg_artifact_envelope_publication ce",
        "source_system",
        "source_version",
        "source_family",
        "status_family",
        "source_table",
        "conflict_key",
    ):
        assert required_fragment in text


def test_status_rows_are_grouped_by_source_table_and_conflict_key() -> None:
    text = _migration_text()

    for required_fragment in (
        "'normalized_publication_rows'::TEXT AS status_family",
        "'review_status_patch_rows'::TEXT AS status_family",
        "'cdg_artifact_envelope_rows'::TEXT AS status_family",
        "'public.artifact_symbolic_expressions'::TEXT AS source_table",
        "'public.artifact_versions'::TEXT AS source_table",
        "'version_id,expression_role,source_expression_id'::TEXT AS conflict_key",
        "'artifact_id,content_hash'::TEXT AS conflict_key",
        "GROUP BY source_system, source_version, source_family",
    ):
        assert required_fragment in text


def test_normalized_publication_and_review_patch_readiness_counts_are_exposed() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "COUNT(*) FILTER ( WHERE version_id IS NOT NULL AND expression_role <> '' AND source_expression_id <> '' ) AS conflict_keyed_row_count",
        "COUNT(*) FILTER ( WHERE parse_status = 'normalized' ) AS normalized_row_count",
        "COUNT(*) FILTER ( WHERE parse_status = 'normalized' AND replay_ready ) AS normalized_replay_ready_row_count",
        "COUNT(*) FILTER ( WHERE parse_status IN ('parsed', 'normalized') AND validation_status = 'passed' AND source_expression_id <> '' ) AS review_patch_ready_row_count",
        "COUNT(*) FILTER ( WHERE review_status IN ('automated_pass', 'human_reviewed') ) AS review_patch_applied_row_count",
        "COUNT(*) FILTER ( WHERE parse_status IN ('parsed', 'normalized') AND validation_status = 'passed' AND source_expression_id <> '' AND review_status NOT IN ('automated_pass', 'human_reviewed') ) AS review_patch_pending_row_count",
        "COUNT(*) FILTER ( WHERE retrieval_ready ) AS publication_ready_row_count",
    ):
        assert required_fragment in text


def test_cdg_envelope_status_rows_track_replay_and_publication_blockers() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "COUNT(*) FILTER ( WHERE artifact_id IS NOT NULL AND content_hash <> '' ) AS conflict_keyed_row_count",
        "COUNT(*) FILTER ( WHERE content_hash <> '' AND semver <> '' ) AS normalized_row_count",
        "'symbolic_replay_keys_incomplete' = ANY(publication_blockers)",
        "'cdg_binding_replay_keys_incomplete' = ANY(publication_blockers)",
        "COUNT(*) FILTER ( WHERE publication_ready ) AS publication_ready_row_count",
        "COUNT(*) FILTER ( WHERE NOT publication_ready ) AS blocked_row_count",
    ):
        assert required_fragment in text


def test_observability_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_source_family TEXT DEFAULT NULL",
        "request_source_table TEXT DEFAULT NULL",
        "'status_rows'",
        "'summary'",
        "request_source_family IS NULL OR row_data.source_family = request_source_family",
        "request_source_table IS NULL OR row_data.source_table = request_source_table",
        "ORDER BY row_data.source_system, row_data.source_version, row_data.source_family, row_data.source_table, row_data.conflict_key, row_data.status_family",
        "'status_row_count'",
        "'source_row_count'",
        "'conflict_keyed_row_count'",
        "'normalized_replay_ready_row_count'",
        "'review_patch_pending_row_count'",
        "'publication_ready_row_count'",
        "GRANT SELECT ON public.physics_backfill_review_publication_status_rows TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_backfill_review_publication_observability(TEXT, TEXT) TO authenticated",
    ):
        assert required_fragment in text
