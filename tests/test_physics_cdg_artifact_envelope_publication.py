from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR / "20260501030000_physics_cdg_artifact_envelope_publication.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_cdg_artifact_envelope_migration_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_cdg_artifact_envelope_publication" in text
    assert (
        "CREATE OR REPLACE FUNCTION public.physics_cdg_artifact_envelope_publication("
        in text
    )
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text


def test_cdg_artifact_envelope_runs_after_existing_physics_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260414000000_unified_artifacts_phase1.sql",
        "20260430000000_physics_symbolic_ingestion_wave0.sql",
        "20260430020000_physics_symbolic_operational_publication_audit.sql",
        "20260501000000_physics_symbolic_write_plan_operational_support.sql",
        "20260501020000_physics_symbolic_retrieval_support.sql",
        "20260501030000_physics_cdg_artifact_envelope_publication.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_cdg_artifact_envelope_joins_artifacts_versions_cdg_and_symbolic_rows() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.artifacts a",
        "JOIN public.artifact_versions v",
        "WHERE a.artifact_kind = 'cdg'",
        "public.artifact_cdg_nodes n",
        "public.artifact_cdg_edges e",
        "public.artifact_cdg_bindings b",
        "public.artifact_symbolic_expressions se",
        "public.artifact_audit_rollups ar",
        "content_hash",
        "semver",
        "is_latest",
        "cdg_node_count",
        "cdg_edge_count",
        "cdg_binding_count",
        "replay_keyed_binding_count",
        "symbolic_expression_count",
    ):
        assert required_fragment in text


def test_cdg_artifact_envelope_surfaces_review_status_and_replay_blockers() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "COALESCE(ar.review_status, 'missing') AS artifact_review_status",
        "vs.artifact_review_status <> 'approved'",
        "'artifact_review_status_not_approved'",
        "se.review_status IN ('automated_pass', 'human_reviewed')",
        "se.review_status = 'needs_human'",
        "'symbolic_review_status_update_needed'",
        "se.source_expression_id <> ''",
        "se.canonical_expr_hash <> ''",
        "se.topology_hash <> ''",
        "se.dimensional_hash <> ''",
        "'symbolic_replay_keys_incomplete'",
        "b.bound_artifact_fqdn <> ''",
        "b.bound_version_content_hash <> ''",
        "'cdg_binding_replay_keys_incomplete'",
        "publication_blockers",
        "publication_ready",
    ):
        assert required_fragment in text


def test_cdg_artifact_envelope_rpc_filters_latest_rows_and_summarizes_gaps() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_fqdn TEXT DEFAULT NULL",
        "request_latest_only BOOLEAN DEFAULT TRUE",
        "'cdg_artifact_envelopes'",
        "'summary'",
        "'cdg_version_count'",
        "'publication_ready_version_count'",
        "'review_status_update_needed_count'",
        "'replay_key_incomplete_count'",
        "request_fqdn IS NULL OR row_data.fqdn = request_fqdn",
        "NOT request_latest_only OR row_data.is_latest",
        "GRANT SELECT ON public.physics_cdg_artifact_envelope_publication TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_cdg_artifact_envelope_publication(TEXT, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
