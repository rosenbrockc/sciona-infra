from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS_DIR / "20260501020000_physics_symbolic_retrieval_support.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _compact_sql(text: str) -> str:
    return " ".join(text.split())


def test_symbolic_retrieval_support_migration_is_read_only_and_idempotent() -> None:
    text = _migration_text()

    assert "CREATE OR REPLACE VIEW public.physics_symbolic_retrieval_rows" in text
    assert (
        "CREATE OR REPLACE VIEW public.physics_symbolic_source_retrieval_replay_readiness"
        in text
    )
    assert "CREATE OR REPLACE FUNCTION public.physics_symbolic_retrieval_index(" in text
    assert "CREATE TABLE" not in text
    assert "ALTER TABLE" not in text
    assert "DROP TABLE" not in text
    assert "DROP VIEW" not in text
    assert "CREATE TRIGGER" not in text
    assert "CREATE POLICY" not in text
    assert "CREATE UNIQUE INDEX" not in text


def test_symbolic_retrieval_support_runs_after_existing_physics_surfaces() -> None:
    migration_names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    required_order = [
        "20260430000000_physics_symbolic_ingestion_wave0.sql",
        "20260430010000_physics_symbolic_ingestion_wave4_dashboard_coverage.sql",
        "20260430020000_physics_symbolic_operational_publication_audit.sql",
        "20260501000000_physics_symbolic_write_plan_operational_support.sql",
        "20260501010000_physics_symbolic_bulk_backfill_normalization_coverage.sql",
        "20260501020000_physics_symbolic_retrieval_support.sql",
    ]
    positions = [migration_names.index(name) for name in required_order]

    assert positions == sorted(positions)


def test_retrieval_rows_expose_synthesis_lookup_dimensions() -> None:
    text = _migration_text()

    for required_fragment in (
        "topology_hash",
        "dimensional_hash",
        "mechanism_tags",
        "behavioral_archetypes",
        "source_system",
        "source_version",
        "source_family",
        "source_uri",
        "source_candidate_id",
        "source_expression_id",
        "overall_verdict",
        "risk_tier",
        "risk_score",
        "acceptability_score",
        "trust_readiness",
        "trust_blockers",
        "raw_suggestion_count",
        "raw_imported_suggestion_count",
        "blocked_or_failed_suggestion_count",
    ):
        assert required_fragment in text


def test_retrieval_rows_join_current_symbolic_source_and_trust_tables() -> None:
    text = _migration_text()

    for required_fragment in (
        "FROM public.artifact_symbolic_expressions se",
        "JOIN public.artifacts a",
        "JOIN public.artifact_versions v",
        "public.physics_ingest_snapshots s",
        "public.physics_equation_candidates c",
        "public.artifact_symbolic_variables sv",
        "public.artifact_relationships",
        "LEFT JOIN public.artifact_audit_rollups ar",
        "LEFT JOIN public.atom_source_repositories sr",
        "source_repo_name",
        "source_repo_url",
        "source_package",
        "source_module_path",
        "source_symbol",
    ):
        assert required_fragment in text


def test_retrieval_readiness_requires_topology_dimensions_validation_and_trust() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "se.topology_hash <> ''",
        "se.dimensional_hash <> ''",
        "COALESCE(vr.retrieval_variable_count, 0) > 0",
        "COALESCE(vr.missing_dimension_signature_count, 0) = 0",
        "COALESCE(vr.unknown_dimension_source_count, 0) = 0",
        "se.parse_status IN ('parsed', 'normalized')",
        "se.review_status IN ('automated_pass', 'human_reviewed')",
        "se.validation_status = 'passed'",
        "COALESCE(ar.overall_verdict, 'unknown') NOT IN ('broken', 'misleading')",
        "COALESCE(ar.trust_readiness, 'not_ready') IN ( 'reviewed_with_limits', 'catalog_ready', 'ready_for_manifest_merge', 'ready' )",
        "retrieval_ready",
        "replay_ready",
    ):
        assert required_fragment in text


def test_source_family_retrieval_replay_readiness_is_covered_without_schema_churn() -> None:
    text = _migration_text()

    for required_fragment in (
        "public.physics_symbolic_source_retrieval_replay_readiness",
        "snapshot_count",
        "source_uri_count",
        "license_expression_count",
        "payload_sha256_count",
        "raw_suggestion_count",
        "source_candidate_id_ready_count",
        "symbolic_expression_count",
        "retrieval_ready_expression_count",
        "replay_ready_expression_count",
        "source_expression_id_ready_count",
        "topology_ready_expression_count",
        "dimensional_hash_ready_expression_count",
    ):
        assert required_fragment in text


def test_retrieval_rpc_supports_filtering_and_deterministic_ordering() -> None:
    text = _compact_sql(_migration_text())

    for required_fragment in (
        "request_topology_hash TEXT DEFAULT NULL",
        "request_dimensional_hash TEXT DEFAULT NULL",
        "request_mechanism_tag TEXT DEFAULT NULL",
        "request_source_family TEXT DEFAULT NULL",
        "request_trust_readiness TEXT DEFAULT NULL",
        "'symbolic_rows'",
        "'source_replay_readiness'",
        "request_topology_hash IS NULL OR row_data.topology_hash = request_topology_hash",
        "request_dimensional_hash IS NULL OR row_data.dimensional_hash = request_dimensional_hash",
        "request_mechanism_tag IS NULL OR request_mechanism_tag = ANY(row_data.mechanism_tags)",
        "request_source_family IS NULL OR row_data.source_family = request_source_family",
        "request_trust_readiness IS NULL OR row_data.trust_readiness = request_trust_readiness",
        "ORDER BY row_data.retrieval_ready DESC, row_data.priority_score DESC, row_data.fqdn, row_data.semver, row_data.expression_role, row_data.expression_id",
    ):
        assert required_fragment in text


def test_retrieval_support_grants_authenticated_read_access() -> None:
    text = _migration_text()

    for required_fragment in (
        "GRANT SELECT ON public.physics_symbolic_retrieval_rows TO authenticated",
        "GRANT SELECT ON public.physics_symbolic_source_retrieval_replay_readiness TO authenticated",
        "GRANT EXECUTE ON FUNCTION public.physics_symbolic_retrieval_index(TEXT, TEXT, TEXT, TEXT, TEXT) TO authenticated",
    ):
        assert required_fragment in text
