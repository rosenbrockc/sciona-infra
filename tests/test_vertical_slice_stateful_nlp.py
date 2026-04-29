from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from conftest_state_artifacts import fixture_path
from sciona_infra.assets.hydrator import (
    ResolvedAsset,
    ResolvedDependency,
    write_lockfile,
)
from sciona_infra.assets.resolver import AssetFile, AssetRef, hydrate_asset
from sciona_infra.audit.tier_gates import tier1_readiness_check, tier2_automated_gate


TAXONOMY_HASH = "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1"
LOGIC_ATOM_HASH = "c" * 64


def _load_taxonomy_matcher():
    module_path = fixture_path("taxonomy_entity_match.py")
    spec = importlib.util.spec_from_file_location("taxonomy_entity_match_fixture", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.taxonomy_entity_match


def _evidence(audit_type: str) -> dict[str, object]:
    return {
        "audit_type": audit_type,
        "passed": True,
        "status": "completed",
        "details": {"vertical_slice": True},
    }


def test_end_to_end_stateful_nlp_vertical_slice(tmp_path: Path) -> None:
    taxonomy_path = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash=TAXONOMY_HASH,
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256=TAXONOMY_HASH,
                byte_size=taxonomy_path.stat().st_size,
                storage_uri=taxonomy_path.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    hydrated_path = hydrate_asset(ref, cache_dir=tmp_path)
    taxonomy = json.loads(hydrated_path.read_text(encoding="utf-8"))["entries"]
    matcher = _load_taxonomy_matcher()

    first = matcher("OpenAI is a company and research laboratory.", taxonomy)
    second = matcher("OpenAI is a company and research laboratory.", taxonomy)

    assert first == second
    assert first[:2] == [
        {
            "entity_type": "Organization",
            "confidence": 1.0,
            "span_start": 12,
            "span_end": 19,
        },
        {
            "entity_type": "ResearchOrganization",
            "confidence": 1.0,
            "span_start": 33,
            "span_end": 43,
        },
    ]

    dependency = ResolvedDependency(
        fqdn=ref.fqdn,
        content_hash=ref.content_hash,
        assets=(
            ResolvedAsset(
                path="taxonomy.json",
                sha256=TAXONOMY_HASH,
                cached_at="2026-04-29T00:00:00Z",
                cache_path=hydrated_path,
            ),
        ),
    )
    lockfile = write_lockfile(
        "sciona.cdgs.nlp.org_taxonomy_match.en.v1",
        (dependency,),
        cache_dir=tmp_path,
        content_hash="d" * 64,
    )
    receipt = {
        "logic_atom_hashes": {
            "sciona.atoms.nlp.taxonomy_entity_match": LOGIC_ATOM_HASH,
        },
        "state_artifact_hashes": {
            ref.fqdn: TAXONOMY_HASH,
        },
        "hydration_mode": "aot_strict",
        "lockfile": str(lockfile),
    }
    assert receipt["state_artifact_hashes"][ref.fqdn] == TAXONOMY_HASH
    assert lockfile.exists()

    artifact_doc = {
        "audit_evidence": [
            _evidence("asset_integrity_check"),
            _evidence("format_security_scan"),
            _evidence("loader_policy_check"),
            _evidence("golden_eval"),
            _evidence("determinism_replay"),
        ],
        "dependencies": [
            {
                "dependency_artifact_fqdn": ref.fqdn,
                "dependency_content_hash": TAXONOMY_HASH,
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            },
            {
                "dependency_artifact_fqdn": "sciona.atoms.nlp.taxonomy_entity_match",
                "dependency_content_hash": LOGIC_ATOM_HASH,
                "dependency_role": "logic_atom",
            },
        ],
    }

    assert tier2_automated_gate(artifact_doc).passed
    tier1 = tier1_readiness_check(artifact_doc)
    assert not tier1.passed
    assert "provenance_review" in tier1.missing_evidence
