from __future__ import annotations

from sciona_infra.audit.tier_gates import (
    tier1_readiness_check,
    tier2_automated_gate,
)


PINNED_HASH = "a" * 64


def _evidence(audit_type: str, passed: bool = True) -> dict[str, object]:
    return {
        "audit_type": audit_type,
        "passed": passed,
        "runner_version": "test",
        "details": {"fixture": True},
    }


def _automated_evidence() -> list[dict[str, object]]:
    return [
        _evidence("asset_integrity_check"),
        _evidence("format_security_scan"),
        _evidence("loader_policy_check"),
        _evidence("golden_eval"),
        _evidence("determinism_replay"),
    ]


def _expert_evidence() -> list[dict[str, object]]:
    return [
        _evidence("provenance_review"),
        _evidence("license_ip_review"),
        _evidence("privacy_review"),
        _evidence("golden_eval"),
        _evidence("boundary_review"),
    ]


def test_missing_automated_evidence_blocks_tier2() -> None:
    artifact_doc = {
        "audit_evidence": [
            _evidence("asset_integrity_check"),
            _evidence("format_security_scan"),
            _evidence("loader_policy_check"),
        ],
    }

    result = tier2_automated_gate(artifact_doc)

    assert not result.passed
    assert result.missing_evidence == ("golden_eval", "determinism_replay")


def test_unpinned_or_missing_dependency_hash_blocks_tier1() -> None:
    artifact_doc = {
        "audit_evidence": _expert_evidence(),
        "dependencies": [
            {
                "dependency_artifact_fqdn": "sciona.resources.nlp.org_taxonomy.en.v1",
                "dependency_content_hash": "",
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            },
            {
                "dependency_artifact_fqdn": "sciona.atoms.nlp.taxonomy_entity_match",
                "dependency_role": "logic_atom",
                "port_name": "matcher",
            },
        ],
    }

    result = tier1_readiness_check(artifact_doc)

    assert not result.passed
    assert result.unpinned_dependencies == (
        "sciona.resources.nlp.org_taxonomy.en.v1:taxonomy",
        "sciona.atoms.nlp.taxonomy_entity_match:matcher",
    )


def test_valid_evidence_passes_automated_gate_from_supabase_like_record() -> None:
    supabase_record = {
        "artifact_audit_evidence": _automated_evidence(),
    }

    result = tier2_automated_gate(supabase_record)

    assert result.passed
    assert result.missing_evidence == ()
    assert result.failed_evidence == ()


def test_no_expert_review_yet_blocks_tier1() -> None:
    artifact_doc = {
        "audit_evidence": _automated_evidence(),
        "dependencies": [
            {
                "dependency_artifact_fqdn": "sciona.resources.nlp.org_taxonomy.en.v1",
                "dependency_content_hash": PINNED_HASH,
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            },
        ],
    }

    result = tier1_readiness_check(artifact_doc)

    assert not result.passed
    assert result.unpinned_dependencies == ()
    assert result.missing_evidence == (
        "provenance_review",
        "license_ip_review",
        "privacy_review",
        "boundary_review",
    )
