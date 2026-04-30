from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260430000000_physics_symbolic_ingestion_wave0.sql"
)


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_physics_symbolic_ingestion_wave0_defines_core_tables() -> None:
    text = _migration_text()

    assert "ADD COLUMN IF NOT EXISTS dim_signature" in text

    for table_name in (
        "physics_ingest_snapshots",
        "physics_equation_candidates",
        "artifact_symbolic_expressions",
        "artifact_symbolic_variables",
        "artifact_validity_bounds",
        "artifact_relationships",
    ):
        assert f"CREATE TABLE IF NOT EXISTS public.{table_name}" in text
        assert f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY" in text


def test_physics_symbolic_ingestion_wave0_covers_sources_and_states() -> None:
    text = _migration_text()

    for source_system in (
        "wikidata",
        "qudt",
        "physics_derivation_graph",
        "nist_codata",
        "nist_dlmf",
        "hitran",
        "materials_project",
        "opb",
        "theoria",
        "phy_srbench",
    ):
        assert f"'{source_system}'" in text

    for review_state in (
        "raw_imported",
        "dimension_resolved",
        "symbolically_validated",
        "source_verified",
        "human_reviewed",
        "published",
    ):
        assert f"'{review_state}'" in text


def test_physics_symbolic_ingestion_wave0_covers_symbolic_contract() -> None:
    text = _migration_text()

    for column_name in (
        "sympy_srepr",
        "canonical_expr_hash",
        "topology_hash",
        "dimensional_hash",
        "mechanism_tags",
        "behavioral_archetypes",
        "quantity_kind_uri",
        "unit_uri",
        "dim_signature",
    ):
        assert column_name in text

    for relationship_kind in (
        "same_math_topology_as",
        "physical_grounding_of",
        "derives_from",
        "limit_case_of",
        "approximation_of",
        "uses_constant",
        "uses_data_artifact",
        "has_use",
        "mechanism_analogue_of",
        "algebraic_rearrangement_of",
        "requires_assumption",
    ):
        assert f"'{relationship_kind}'" in text


def test_physics_symbolic_ingestion_wave0_extends_document_and_coverage_surfaces() -> None:
    text = _migration_text()

    assert "CREATE VIEW public.catalog_symbolic_artifacts" in text
    assert "CREATE OR REPLACE FUNCTION public.get_artifact_document" in text
    assert "'symbolic_expressions'" in text
    assert "'symbolic_variables'" in text
    assert "'validity_bounds'" in text
    assert "'relationships'" in text
    assert "CREATE OR REPLACE FUNCTION public.symbolic_artifact_coverage" in text
