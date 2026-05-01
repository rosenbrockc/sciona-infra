from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = MIGRATIONS_DIR / "20260501060000_physics_cdg_graph_readiness.sql"


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_cdg_graph_readiness_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_cdg_graph_readiness" in text
    assert "CREATE OR REPLACE FUNCTION public.physics_cdg_graph_readiness(" in text
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


def test_cdg_graph_readiness_runs_after_existing_cdg_publication_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260414000000_unified_artifacts_phase1.sql",
        "20260501030000_physics_cdg_artifact_envelope_publication.sql",
        "20260501050000_physics_source_retrieval_backfill_readiness.sql",
        "20260501060000_physics_cdg_graph_readiness.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_cdg_graph_readiness_composes_artifacts_versions_and_cdg_tables() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.artifacts a",
        "JOIN public.artifact_versions v",
        "WHERE a.artifact_kind = 'cdg'",
        "FROM public.artifact_cdg_nodes n",
        "FROM public.artifact_cdg_edges e",
        "FROM public.artifact_cdg_bindings b",
        "vs.artifact_id",
        "vs.fqdn",
        "vs.version_id",
        "vs.content_hash",
        "vs.semver",
        "vs.is_latest",
        "node_count",
        "edge_count",
        "binding_count",
    ):
        assert required_fragment in text


def test_cdg_graph_readiness_surfaces_endpoint_binding_and_duplicate_blockers() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "source_node.version_id = e.version_id",
        "source_node.node_id = e.source_id",
        "target_node.version_id = e.version_id",
        "target_node.node_id = e.target_id",
        "bound_node.version_id = b.version_id",
        "bound_node.node_id = b.node_id",
        "missing_edge_source_count",
        "missing_edge_target_count",
        "missing_edge_endpoint_count",
        "missing_binding_node_count",
        "'missing_edge_source_nodes'",
        "'missing_edge_target_nodes'",
        "'missing_binding_nodes'",
        "duplicate_node_key_count",
        "duplicate_edge_key_count",
        "duplicate_binding_key_count",
        "duplicate_key_count",
        "'duplicate_node_keys'",
        "'duplicate_edge_keys'",
        "'duplicate_binding_keys'",
        "blockers",
        "blocker_count",
        "graph_ready",
        "readiness_status",
    ):
        assert required_fragment in text


def test_cdg_graph_readiness_duplicate_keys_are_version_scoped() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "GROUP BY n.version_id, n.node_id",
        "GROUP BY e.version_id, e.source_id, e.target_id, e.output_name, e.input_name",
        "GROUP BY b.version_id, b.node_id, b.bound_artifact_fqdn",
        "SUM(row_count - 1) FILTER (WHERE row_count > 1)",
    ):
        assert required_fragment in text


def test_cdg_graph_readiness_rpc_filters_orders_summarizes_and_grants() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_fqdn TEXT DEFAULT NULL",
        "request_blocker TEXT DEFAULT NULL",
        "request_latest_only BOOLEAN DEFAULT TRUE",
        "request_ready_only BOOLEAN DEFAULT FALSE",
        "'cdg_graph_readiness'",
        "'summary'",
        "request_fqdn IS NULL OR row_data.fqdn = request_fqdn",
        "request_blocker IS NULL OR request_blocker = ANY(row_data.blockers)",
        "NOT request_latest_only OR row_data.is_latest",
        "NOT request_ready_only OR row_data.graph_ready",
        "ORDER BY row_data.graph_ready DESC, row_data.blocker_count, row_data.fqdn, row_data.is_latest DESC, row_data.semver, row_data.version_id",
        "'cdg_version_count'",
        "'graph_ready_version_count'",
        "'missing_edge_endpoint_count'",
        "'missing_binding_node_count'",
        "'duplicate_key_count'",
        "'blocker_count'",
        "GRANT SELECT ON public.physics_cdg_graph_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_cdg_graph_readiness(TEXT, TEXT, BOOLEAN, BOOLEAN) TO authenticated",
    ):
        assert required_fragment in text
