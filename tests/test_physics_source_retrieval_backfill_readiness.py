from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR
    / "20260501050000_physics_source_retrieval_backfill_readiness.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_source_retrieval_backfill_readiness_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert (
        "CREATE OR REPLACE VIEW public.physics_source_retrieval_backfill_readiness"
        in text
    )
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_source_retrieval_backfill_observability("
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text


def test_source_retrieval_backfill_readiness_runs_after_existing_observability() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql",
        "20260501020000_physics_symbolic_retrieval_support.sql",
        "20260501030000_physics_cdg_artifact_envelope_publication.sql",
        "20260501040000_physics_backfill_review_publication_observability.sql",
        "20260501050000_physics_source_retrieval_backfill_readiness.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_readiness_view_composes_existing_physics_observability_surfaces() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.physics_symbolic_retrieval_rows",
        "FROM public.physics_symbolic_source_retrieval_replay_readiness",
        "FROM public.physics_backfill_review_publication_status_rows",
        "source_system",
        "source_version",
        "source_family",
        "source_keys",
    ):
        assert required_fragment in text


def test_readiness_view_exposes_required_counts_and_blocker_categories() -> None:
    text = _migration_text()

    for required_fragment in (
        "source_row_count",
        "replay_ready_row_count",
        "retrieval_ready_row_count",
        "review_pending_row_count",
        "provenance_blocked_row_count",
        "license_blocked_row_count",
        "replay_blocked_row_count",
        "retrieval_blocked_row_count",
        "publication_blocked_row_count",
        "blockers",
        "blocker_count",
        "'provenance_gap'",
        "'license_gap'",
        "'replay_gap'",
        "'review_pending'",
        "'publication_blocked'",
        "'source_suggestions_blocked_or_failed'",
        "backfill_ready",
    ):
        assert required_fragment in text


def test_provenance_license_replay_and_review_gap_logic_is_explicit() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "WHERE source_uri = '' OR payload_sha256 = '' OR source_candidate_id = '' OR source_expression_id = ''",
        "WHERE license_expression = ''",
        "WHERE NOT replay_ready",
        "WHERE NOT retrieval_ready",
        "WHERE parse_status IN ('parsed', 'normalized') AND validation_status = 'passed' AND source_expression_id <> '' AND review_status NOT IN ('automated_pass', 'human_reviewed')",
        "SUM(review_patch_pending_row_count) FILTER ( WHERE status_family = 'review_status_patch_rows' ) AS publication_review_pending_row_count",
        "SUM(blocked_row_count) FILTER ( WHERE status_family IN ( 'normalized_publication_rows', 'cdg_artifact_envelope_rows' ) ) AS publication_blocked_row_count",
        "GREATEST( COALESCE(rr.review_pending_row_count, 0), COALESCE(pr.publication_review_pending_row_count, 0) ) AS review_pending_row_count",
    ):
        assert required_fragment in text


def test_readiness_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_source_family TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "'source_readiness'",
        "'summary'",
        "request_source_family IS NULL OR row_data.source_family = request_source_family",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_ready_only OR row_data.backfill_ready",
        "ORDER BY row_data.backfill_ready DESC, row_data.blocker_count, row_data.source_system, row_data.source_version, row_data.source_family",
        "'source_family_count'",
        "'source_row_count'",
        "'replay_ready_row_count'",
        "'retrieval_ready_row_count'",
        "'review_pending_row_count'",
        "'blocker_count'",
        "GRANT SELECT ON public.physics_source_retrieval_backfill_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_source_retrieval_backfill_observability(TEXT, TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
