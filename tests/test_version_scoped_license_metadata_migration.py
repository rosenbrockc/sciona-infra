from __future__ import annotations

from pathlib import Path


def test_version_scoped_license_metadata_migration_defines_atom_and_artifact_tables() -> None:
    migration = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "20260415000000_version_scoped_license_metadata.sql"
    text = migration.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS public.atom_version_license_metadata" in text
    assert "CREATE TABLE IF NOT EXISTS public.artifact_version_license_metadata" in text
    assert "license_expression TEXT NOT NULL DEFAULT ''" in text
    assert "license_status TEXT NOT NULL DEFAULT 'unknown'" in text
    assert "license_family TEXT NOT NULL DEFAULT 'unknown'" in text
    assert "license_source_path TEXT NOT NULL DEFAULT ''" in text
