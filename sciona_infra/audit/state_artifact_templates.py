"""Structured audit templates for state artifact evidence.

These templates describe the evidence payload expected in
``artifact_audit_evidence.details`` for state artifact tier reviews. They are
plain dictionaries so workers, tests, and future API models can consume them
without importing a validation framework.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATE_ARTIFACT_AUDIT_TYPES = (
    "asset_integrity_check",
    "format_security_scan",
    "loader_policy_check",
    "provenance_review",
    "license_ip_review",
    "privacy_review",
    "golden_eval",
    "boundary_review",
    "determinism_replay",
)

AUTOMATED_EVIDENCE_TYPES = (
    "asset_integrity_check",
    "format_security_scan",
    "loader_policy_check",
    "golden_eval",
    "determinism_replay",
)

EXPERT_REVIEW_EVIDENCE_TYPES = (
    "provenance_review",
    "license_ip_review",
    "privacy_review",
    "golden_eval",
    "boundary_review",
)


AUDIT_TEMPLATES: dict[str, dict[str, Any]] = {
    "asset_integrity_check": {
        "audit_type": "asset_integrity_check",
        "review_mode": "automated",
        "required_fields": (
            "declared_sha256",
            "computed_sha256",
            "asset_path",
            "byte_size",
        ),
        "checklist": (
            "computed hash matches declared asset hash",
            "byte size matches registry metadata",
            "multi-file manifest hash was recomputed when applicable",
        ),
    },
    "format_security_scan": {
        "audit_type": "format_security_scan",
        "review_mode": "automated",
        "required_fields": (
            "declared_format",
            "detected_format",
            "magic_bytes_ok",
            "allowlisted",
        ),
        "checklist": (
            "format is allowlisted",
            "extension and magic bytes match declared format",
            "asset does not masquerade as a safer format",
        ),
    },
    "loader_policy_check": {
        "audit_type": "loader_policy_check",
        "review_mode": "automated",
        "required_fields": (
            "loader_name",
            "network_access",
            "blocked_serializers",
        ),
        "checklist": (
            "loader can materialize without network access",
            "pickle, joblib, torch pickle paths, and arbitrary code loaders are blocked",
            "format-specific safe loader options are enforced",
        ),
    },
    "provenance_review": {
        "audit_type": "provenance_review",
        "review_mode": "expert",
        "required_fields": (
            "training_data_origin",
            "pipeline_description",
            "data_license_compatibility",
        ),
        "checklist": (
            "training data origin is documented",
            "resource construction pipeline is reproducible or externally attested",
            "data licenses are compatible with the requested tier",
        ),
    },
    "license_ip_review": {
        "audit_type": "license_ip_review",
        "review_mode": "expert",
        "required_fields": (
            "license_basis",
            "copyright_screening",
            "fre_shield_applicability",
        ),
        "checklist": (
            "no unlicensed restricted copyrighted material is known in the resource",
            "license basis covers registry distribution and certified use",
            "FRE shield applicability has been assessed",
        ),
    },
    "privacy_review": {
        "audit_type": "privacy_review",
        "review_mode": "expert",
        "required_fields": (
            "pii_scan_results",
            "anonymization_method",
            "data_retention_policy",
        ),
        "checklist": (
            "PII and HIPAA-sensitive content scans are recorded",
            "anonymization or de-identification method is documented",
            "retention and deletion policy is compatible with certified use",
        ),
    },
    "golden_eval": {
        "audit_type": "golden_eval",
        "review_mode": "automated_or_expert",
        "required_fields": (
            "suite_id",
            "expected_vs_actual",
            "deterministic_outputs",
        ),
        "checklist": (
            "visible or hidden golden suite passed",
            "expected and actual outputs are recorded",
            "outputs satisfy deterministic rounding requirements",
        ),
    },
    "boundary_review": {
        "audit_type": "boundary_review",
        "review_mode": "expert",
        "required_fields": (
            "declared_limitations",
            "degradation_curves",
            "out_of_bound_behavior",
        ),
        "checklist": (
            "certified boundaries are declared in metadata",
            "accuracy degradation is characterized outside certified limits",
            "out-of-bound behavior is explicit and non-covered",
        ),
    },
    "determinism_replay": {
        "audit_type": "determinism_replay",
        "review_mode": "automated",
        "required_fields": (
            "cross_run_comparison",
            "rounding_precision",
            "hardware_independence",
        ),
        "checklist": (
            "same-hardware replay is byte-identical after declared rounding",
            "floating point precision policy is recorded",
            "hardware-specific assumptions are documented",
        ),
    },
}


def audit_template(audit_type: str) -> dict[str, Any]:
    """Return a copy of the structured template for ``audit_type``."""

    try:
        return deepcopy(AUDIT_TEMPLATES[audit_type])
    except KeyError as exc:
        raise ValueError(f"unknown state artifact audit type: {audit_type}") from exc
