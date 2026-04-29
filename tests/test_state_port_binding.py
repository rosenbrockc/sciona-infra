from __future__ import annotations

import json
import sys
from pathlib import Path

from sciona_infra.audit.tier_gates import tier1_readiness_check


PINNED_HASH = "a" * 64


def _expert_and_automated_evidence() -> list[dict[str, object]]:
    types = [
        "asset_integrity_check",
        "format_security_scan",
        "loader_policy_check",
        "golden_eval",
        "determinism_replay",
        "provenance_review",
        "license_ip_review",
        "privacy_review",
        "boundary_review",
    ]
    return [
        {
            "audit_type": t,
            "passed": True,
            "runner_version": "test",
            "details": {"fixture": True},
        }
        for t in types
    ]


def test_pinned_state_artifact_dependency_passes_tier1() -> None:
    artifact_doc = {
        "audit_evidence": _expert_and_automated_evidence(),
        "artifact_dependencies": [
            {
                "dependency_artifact_fqdn": "sciona.resources.nlp.org_taxonomy.en.v1",
                "dependency_content_hash": PINNED_HASH,
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            },
        ],
    }

    result = tier1_readiness_check(artifact_doc)

    assert result.passed
    assert result.unpinned_dependencies == ()


def test_unpinned_state_port_dependency_fails_tier1() -> None:
    artifact_doc = {
        "audit_evidence": _expert_and_automated_evidence(),
        "dependencies": [
            {
                "dependency_artifact_fqdn": "sciona.resources.nlp.org_taxonomy.en.v1",
                "dependency_content_hash": "",
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            },
        ],
    }

    result = tier1_readiness_check(artifact_doc)

    assert not result.passed
    assert "sciona.resources.nlp.org_taxonomy.en.v1:taxonomy" in result.unpinned_dependencies


def test_taxonomy_entity_match_golden_output_stability() -> None:
    fixtures_dir = Path(__file__).parent / "fixtures" / "state_artifacts"
    sys.path.insert(0, str(fixtures_dir))
    try:
        from taxonomy_entity_match import taxonomy_entity_match
    finally:
        sys.path.pop(0)

    taxonomy_path = fixtures_dir / "org_taxonomy_en.json"
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))["entries"]
    text = "OpenAI released GPT-4"

    run1 = json.dumps(taxonomy_entity_match(text, taxonomy), sort_keys=True)
    run2 = json.dumps(taxonomy_entity_match(text, taxonomy), sort_keys=True)

    assert run1 == run2
