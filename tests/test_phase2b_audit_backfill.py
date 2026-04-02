"""Focused tests for the Phase 2B audit backfill helpers."""

from __future__ import annotations

from scripts.backfill_audit_evidence import build_evidence_rows
from scripts.backfill_audit_rollups import build_rollup_row


def test_build_evidence_rows_applies_skip_conditions() -> None:
    rows = build_evidence_rows(
        "atom-123",
        {
            "source_revision": "abc123",
            "upstream_version": "v1.2.3",
            "structural_status": "pass",
            "structural_findings": ["ok"],
            "structural_finding_details": ["detail"],
            "semantic_status": "unknown",
            "risk_tier": "medium",
            "risk_score": 4,
            "risk_dimensions": {"ffi_risk": {"score": 2}},
            "risk_reasons": ["ffi"],
            "parity_coverage_level": "none",
            "runtime_status": "not_applicable",
        },
    )

    assert [row["audit_type"] for row in rows] == ["structural_audit", "risk_assessment"]
    assert rows[0]["passed"] is True
    assert rows[1]["details"]["risk_score"] == 4
    assert all(row["runner_version"] == "backfill-v1" for row in rows)


def test_build_evidence_rows_sets_parity_and_smoke_pass_flags() -> None:
    rows = build_evidence_rows(
        "atom-123",
        {
            "semantic_status": "pass",
            "parity_coverage_level": "parity_or_usage_equivalent",
            "parity_test_status": "pass",
            "parity_fixture_count": 3,
            "parity_case_count": 9,
            "usage_test_coverage": "broad",
            "runtime_status": "pass",
            "status_basis": {"runtime": ["smoke"]},
        },
    )

    by_type = {row["audit_type"]: row for row in rows}
    assert by_type["semantic_audit"]["passed"] is True
    assert by_type["parity_check"]["passed"] is True
    assert by_type["parity_check"]["details"]["fixture_count"] == 3
    assert by_type["smoke_test"]["details"]["status_basis"] == ["smoke"]


def test_build_rollup_row_applies_defaults_for_missing_fields() -> None:
    row = build_rollup_row("atom-123", {})

    assert row["atom_id"] == "atom-123"
    assert row["overall_verdict"] == "unknown"
    assert row["risk_tier"] == "medium"
    assert row["review_status"] == "missing"
    assert row["trust_readiness"] == "not_ready"
    assert row["risk_reasons"] == []
    assert row["trust_blockers"] == []
