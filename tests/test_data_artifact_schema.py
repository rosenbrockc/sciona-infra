"""Contracts for the first-class data artifact catalog migration."""

from pathlib import Path


def test_data_artifact_catalog_schema_and_public_rpc() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = root / "supabase/migrations/20260808000000_data_artifact_catalog.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "'data_artifact'" in sql
    assert "CREATE TABLE IF NOT EXISTS public.data_artifact_metadata" in sql
    assert "CREATE TABLE IF NOT EXISTS public.artifact_data_compatibility" in sql
    assert "data_version_id UUID NOT NULL" in sql
    assert "input_port TEXT NOT NULL" in sql
    assert "evidence_json JSONB NOT NULL" in sql
    assert "CREATE OR REPLACE FUNCTION public.catalog_data_artifacts" in sql
    assert "SECURITY DEFINER" in sql
    assert "AND a.visibility_tier = 'general'" in sql
    assert "TO anon, authenticated" in sql


def test_matcher_migration_is_byte_identical() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = Path("supabase/migrations/20260808000000_data_artifact_catalog.sql")

    assert (root / relative).read_bytes() == (root.parent / "sciona-matcher" / relative).read_bytes()


def test_provider_atom_compatibility_uses_stable_fqdn() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = Path(
        "supabase/migrations/20260809000000_data_compatibility_provider_atoms.sql"
    )
    sql = (root / relative).read_text(encoding="utf-8")

    assert "consumer_atom_id UUID REFERENCES public.atoms(atom_id)" in sql
    assert "consumer_atom_version_id UUID" in sql
    assert "consumer_fqdn TEXT NOT NULL" in sql
    assert "num_nonnulls(consumer_artifact_id, consumer_atom_id) = 1" in sql
    assert (root / relative).read_bytes() == (root.parent / "sciona-matcher" / relative).read_bytes()
