from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260428000000_stateful_nlp_atoms.sql"


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_stateful_artifacts_migration_defines_schema_objects() -> None:
    text = _migration_text()

    assert "CHECK (artifact_kind IN ('atom', 'cdg', 'state_artifact'))" in text
    assert "artifacts_state_artifact_shape_check" in text
    assert "CREATE TABLE IF NOT EXISTS public.artifact_assets" in text
    assert "CREATE TABLE IF NOT EXISTS public.state_artifact_metadata" in text
    assert "CREATE TABLE IF NOT EXISTS public.artifact_state_ports" in text
    assert "CREATE TABLE IF NOT EXISTS public.artifact_dependencies" in text

    for allowed_format in (
        "safetensors",
        "onnx",
        "json",
        "jsonl",
        "parquet",
        "npy",
        "npz",
        "txt",
        "vocab",
    ):
        assert f"'{allowed_format}'" in text

    for index_name in (
        "idx_artifact_assets_version",
        "idx_artifact_assets_sha256",
        "idx_state_metadata_family",
        "idx_state_ports_artifact",
        "idx_dependencies_dependent",
        "idx_dependencies_fqdn",
    ):
        assert f"CREATE INDEX IF NOT EXISTS {index_name}" in text


def test_stateful_artifacts_migration_extends_audit_and_rpcs() -> None:
    text = _migration_text()

    for audit_type in (
        "asset_integrity_check",
        "format_security_scan",
        "loader_policy_check",
        "provenance_review",
        "license_ip_review",
        "privacy_review",
        "golden_eval",
        "determinism_replay",
        "boundary_review",
    ):
        assert f"'{audit_type}'" in text

    assert "CREATE OR REPLACE FUNCTION public.artifact_is_publishable" in text
    assert "artifact_kind = 'state_artifact'" in text
    assert "FROM public.artifact_assets aa" in text
    assert "FROM public.state_artifact_metadata sam" in text

    assert "CREATE OR REPLACE FUNCTION public.get_artifact_document" in text
    for section in ("'assets'", "'state_metadata'", "'state_ports'", "'dependencies'"):
        assert section in text

    assert "DROP VIEW IF EXISTS public.catalog_artifacts_served CASCADE" in text
    assert "''::TEXT AS resource_family" in text
    assert "COALESCE(sam.resource_family, '') AS resource_family" in text
    assert "COALESCE(sam.language_tags, ARRAY[]::TEXT[]) AS language_tags" in text
    assert "CREATE OR REPLACE FUNCTION public.search_artifacts_hybrid" in text
    assert "CREATE OR REPLACE FUNCTION public.state_artifact_tier2_gate" in text


async def _connect(db_url: str):
    import asyncpg

    return await asyncpg.connect(dsn=db_url, statement_cache_size=0)


async def _seed_state_artifact_requirements(
    conn: Any,
    *,
    artifact_id: str,
    version_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO public.artifact_descriptions (
            artifact_id,
            kind,
            content,
            language,
            reviewed,
            jargon_score
        )
        VALUES (
            $1::uuid,
            'dejargonized',
            'English organization names mapped to stable taxonomy labels.',
            'en',
            TRUE,
            0.1
        )
        """,
        artifact_id,
    )
    await conn.execute(
        """
        INSERT INTO public.references_registry (
            ref_id,
            ref_type,
            title,
            authors,
            year,
            venue
        )
        VALUES (
            'stateful-taxonomy-fixture',
            'standard',
            'Stateful taxonomy fixture',
            ARRAY['Sciona']::text[],
            2026,
            'Local test'
        )
        ON CONFLICT (ref_id) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO public.artifact_references (
            artifact_id,
            ref_id,
            ref_key,
            title,
            authors,
            year,
            verified
        )
        VALUES (
            $1::uuid,
            'stateful-taxonomy-fixture',
            'stateful-taxonomy-fixture',
            'Stateful taxonomy fixture',
            ARRAY['Sciona']::text[],
            2026,
            TRUE
        )
        """,
        artifact_id,
    )
    await conn.execute(
        """
        INSERT INTO public.artifact_audit_rollups (
            artifact_id,
            overall_verdict,
            risk_tier,
            risk_score,
            acceptability_score,
            acceptability_band,
            parity_coverage_level,
            review_status,
            trust_readiness
        )
        VALUES (
            $1::uuid,
            'trusted',
            'low',
            5,
            95,
            'acceptable_with_limits',
            'not_applicable',
            'complete',
            'ready'
        )
        """,
        artifact_id,
    )
    await conn.execute(
        """
        INSERT INTO public.state_artifact_metadata (
            version_id,
            resource_family,
            language_tags,
            vocabulary_size,
            label_schema,
            training_data_summary,
            provenance_summary,
            intended_use,
            limitations,
            legal_basis,
            deterministic_output_precision
        )
        VALUES (
            $1::uuid,
            'taxonomy',
            ARRAY['en']::text[],
            20,
            '{"kind": "ORG_TAXONOMY"}'::jsonb,
            'Small local fixture.',
            'Hand-authored test taxonomy.',
            'Organization entity matching tests.',
            ARRAY['Fixture scale only']::text[],
            '{"basis": "test_fixture"}'::jsonb,
            6
        )
        """,
        version_id,
    )


@pytest.mark.supabase_local
@pytest.mark.asyncio
async def test_local_supabase_state_artifact_schema_and_document_rpc(
    supabase_local_env: dict[str, str],
) -> None:
    conn = await _connect(supabase_local_env["db_url"])
    try:
        artifact_id = str(uuid4())
        version_id = str(uuid4())
        dependent_id = str(uuid4())
        dependent_version_id = str(uuid4())
        suffix = uuid4().hex[:8]
        fqdn = f"sciona.resources.nlp.org_taxonomy.en.{suffix}"
        content_hash = "a" * 64
        dependency_hash = "b" * 64

        await conn.execute(
            """
            INSERT INTO public.artifacts (
                artifact_id,
                artifact_kind,
                fqdn,
                description,
                status,
                stateful_kind
            )
            VALUES (
                $1::uuid,
                'state_artifact',
                $2,
                'English organization taxonomy fixture.',
                'approved',
                'explicit_state_model'
            )
            """,
            artifact_id,
            fqdn,
        )
        await conn.execute(
            """
            INSERT INTO public.artifact_versions (
                version_id,
                artifact_id,
                content_hash,
                semver,
                is_latest
            )
            VALUES ($1::uuid, $2::uuid, $3, '1.0.0', TRUE)
            """,
            version_id,
            artifact_id,
            content_hash,
        )

        assert await conn.fetchval(
            "SELECT public.artifact_is_publishable($1::uuid)",
            artifact_id,
        ) is False

        await _seed_state_artifact_requirements(
            conn,
            artifact_id=artifact_id,
            version_id=version_id,
        )
        await conn.execute(
            """
            INSERT INTO public.artifact_assets (
                version_id,
                asset_path,
                byte_size,
                sha256,
                format,
                media_type,
                storage_uri,
                loader_name
            )
            VALUES (
                $1::uuid,
                'taxonomy.json',
                128,
                $2,
                'json',
                'application/json',
                's3://sciona-test/state/taxonomy.json',
                'json'
            )
            """,
            version_id,
            content_hash,
        )
        await conn.execute(
            """
            INSERT INTO public.artifacts (
                artifact_id,
                artifact_kind,
                fqdn,
                description,
                top_level_input_arity
            )
            VALUES (
                $1::uuid,
                'atom',
                $2,
                'Taxonomy matcher wrapper.',
                1
            )
            """,
            dependent_id,
            f"sciona.atoms.nlp.taxonomy_match.{suffix}",
        )
        await conn.execute(
            """
            INSERT INTO public.artifact_versions (
                version_id,
                artifact_id,
                content_hash,
                semver,
                is_latest
            )
            VALUES ($1::uuid, $2::uuid, $3, '1.0.0', TRUE)
            """,
            dependent_version_id,
            dependent_id,
            dependency_hash,
        )
        await conn.execute(
            """
            INSERT INTO public.artifact_state_ports (
                artifact_id,
                version_id,
                port_name,
                type_desc,
                accepted_formats,
                required_metadata
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                'taxonomy',
                'Organization taxonomy state artifact',
                ARRAY['json']::text[],
                '{"resource_family": "taxonomy"}'::jsonb
            )
            """,
            dependent_id,
            dependent_version_id,
        )
        await conn.execute(
            """
            INSERT INTO public.artifact_dependencies (
                dependent_version_id,
                dependency_artifact_fqdn,
                dependency_content_hash,
                dependency_role,
                port_name
            )
            VALUES (
                $1::uuid,
                $2,
                $3,
                'state_artifact',
                'taxonomy'
            )
            """,
            dependent_version_id,
            fqdn,
            content_hash,
        )

        assert await conn.fetchval(
            "SELECT public.artifact_is_publishable($1::uuid)",
            artifact_id,
        ) is True

        await conn.execute(
            "UPDATE public.artifacts SET is_publishable = TRUE WHERE artifact_id = $1::uuid",
            artifact_id,
        )

        doc = _json_value(
            await conn.fetchval("SELECT public.get_artifact_document($1)", fqdn)
        )
        assert doc["artifact"]["fqdn"] == fqdn
        assert doc["assets"][0]["asset_path"] == "taxonomy.json"
        assert doc["state_metadata"][0]["resource_family"] == "taxonomy"

        dependent_doc = _json_value(
            await conn.fetchval(
                "SELECT public.get_artifact_document($1)",
                f"sciona.atoms.nlp.taxonomy_match.{suffix}",
            )
        )
        assert dependent_doc["state_ports"][0]["port_name"] == "taxonomy"
        assert dependent_doc["dependencies"][0]["dependency_artifact_fqdn"] == fqdn

        catalog_row = await conn.fetchrow(
            """
            SELECT resource_family, language_tags
            FROM public.catalog_artifacts_served
            WHERE fqdn = $1
            """,
            fqdn,
        )
        assert catalog_row is not None
        assert catalog_row["resource_family"] == "taxonomy"
        assert catalog_row["language_tags"] == ["en"]

        gate_before = _json_value(
            await conn.fetchval(
                "SELECT public.state_artifact_tier2_gate($1::uuid)",
                version_id,
            )
        )
        assert gate_before["passed"] is False
        assert "audit_asset_integrity_check" in gate_before["blockers"]

        for audit_type in (
            "asset_integrity_check",
            "format_security_scan",
            "loader_policy_check",
        ):
            await conn.execute(
                """
                INSERT INTO public.artifact_audit_evidence (
                    artifact_id,
                    version_id,
                    audit_type,
                    passed,
                    status,
                    details
                )
                VALUES ($1::uuid, $2::uuid, $3, TRUE, 'completed', '{}'::jsonb)
                """,
                artifact_id,
                version_id,
                audit_type,
            )

        gate_after = _json_value(
            await conn.fetchval(
                "SELECT public.state_artifact_tier2_gate($1::uuid)",
                version_id,
            )
        )
        assert gate_after["passed"] is True
        assert gate_after["blockers"] == []
    finally:
        await conn.close()
