"""Tier gate checks for stateful artifacts and their dependencies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from sciona_infra.audit.state_artifact_templates import (
    AUTOMATED_EVIDENCE_TYPES,
    EXPERT_REVIEW_EVIDENCE_TYPES,
)


PINNED_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class GateResult:
    """Structured result for a tier gate check."""

    passed: bool
    missing_evidence: tuple[str, ...] = ()
    failed_evidence: tuple[str, ...] = ()
    unpinned_dependencies: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence_by_type: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed


def tier2_automated_gate(record: Mapping[str, Any]) -> GateResult:
    """Check automated Tier 2 evidence for an artifact document or DB row."""

    evidence = _evidence_rows(record)
    evidence_by_type, duplicate_failures = _passing_evidence_by_type(evidence)
    evidence_result = require_required_evidence(
        evidence_by_type,
        AUTOMATED_EVIDENCE_TYPES,
        failed_evidence=duplicate_failures,
    )
    return evidence_result


def tier1_readiness_check(record: Mapping[str, Any]) -> GateResult:
    """Check Tier 1 readiness for pinned deps and required expert evidence."""

    evidence = _evidence_rows(record)
    evidence_by_type, duplicate_failures = _passing_evidence_by_type(evidence)
    required_evidence = tuple(
        dict.fromkeys((*AUTOMATED_EVIDENCE_TYPES, *EXPERT_REVIEW_EVIDENCE_TYPES))
    )
    evidence_result = require_required_evidence(
        evidence_by_type,
        required_evidence,
        failed_evidence=duplicate_failures,
    )
    deps_result = require_pinned_dependencies(record)

    blockers = tuple(
        item
        for item in (
            *evidence_result.blockers,
            *deps_result.blockers,
        )
    )
    return GateResult(
        passed=not blockers,
        missing_evidence=evidence_result.missing_evidence,
        failed_evidence=evidence_result.failed_evidence,
        unpinned_dependencies=deps_result.unpinned_dependencies,
        blockers=blockers,
        evidence_by_type=evidence_by_type,
    )


def require_required_evidence(
    evidence_or_record: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    required_types: Iterable[str],
    *,
    failed_evidence: Iterable[str] = (),
) -> GateResult:
    """Require every audit type in ``required_types`` to have passing evidence."""

    if isinstance(evidence_or_record, Mapping) and _looks_like_artifact_record(evidence_or_record):
        evidence_by_type, duplicate_failures = _passing_evidence_by_type(
            _evidence_rows(evidence_or_record)
        )
        failed = tuple(dict.fromkeys((*failed_evidence, *duplicate_failures)))
    elif isinstance(evidence_or_record, Mapping):
        evidence_by_type = evidence_or_record
        failed = tuple(dict.fromkeys(failed_evidence))
    else:
        evidence_by_type, duplicate_failures = _passing_evidence_by_type(evidence_or_record)
        failed = tuple(dict.fromkeys((*failed_evidence, *duplicate_failures)))

    required = tuple(required_types)
    missing = tuple(audit_type for audit_type in required if audit_type not in evidence_by_type)
    blockers = tuple(
        [f"missing required audit evidence: {audit_type}" for audit_type in missing]
        + [f"failed audit evidence: {audit_type}" for audit_type in failed if audit_type in required]
    )
    return GateResult(
        passed=not blockers,
        missing_evidence=missing,
        failed_evidence=tuple(audit_type for audit_type in failed if audit_type in required),
        blockers=blockers,
        evidence_by_type=evidence_by_type,
    )


def require_pinned_dependencies(record: Mapping[str, Any]) -> GateResult:
    """Require all artifact dependencies to pin an immutable SHA-256 hash."""

    dependencies = _dependency_rows(record)
    unpinned = tuple(
        _dependency_label(dependency)
        for dependency in dependencies
        if not _dependency_is_pinned(dependency)
    )
    blockers = tuple(f"dependency is not pinned to a content hash: {label}" for label in unpinned)
    return GateResult(
        passed=not blockers,
        unpinned_dependencies=unpinned,
        blockers=blockers,
    )


def _looks_like_artifact_record(value: Mapping[str, Any]) -> bool:
    record_keys = {
        "audit_evidence",
        "artifact_audit_evidence",
        "evidence",
        "audit",
        "dependencies",
        "artifact_dependencies",
        "versions",
        "artifact_versions",
    }
    return bool(record_keys.intersection(value.keys()))


def _evidence_rows(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    candidates = (
        record.get("audit_evidence"),
        record.get("artifact_audit_evidence"),
        record.get("evidence"),
        _nested_first(record.get("audit"), "evidence"),
        _nested_first(record.get("audit"), "artifact_audit_evidence"),
        _nested_first(record.get("audit_rollup"), "evidence"),
    )
    for candidate in candidates:
        rows = _as_record_tuple(candidate)
        if rows:
            return rows

    rows: list[Mapping[str, Any]] = []
    for version in _as_record_tuple(record.get("versions") or record.get("artifact_versions")):
        rows.extend(_evidence_rows(version))
    return tuple(rows)


def _dependency_rows(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    candidates = (
        record.get("dependencies"),
        record.get("artifact_dependencies"),
        _nested_first(record.get("version"), "dependencies"),
        _nested_first(record.get("version"), "artifact_dependencies"),
    )
    for candidate in candidates:
        rows = _as_record_tuple(candidate)
        if rows:
            return rows

    rows: list[Mapping[str, Any]] = []
    for version in _as_record_tuple(record.get("versions") or record.get("artifact_versions")):
        rows.extend(_dependency_rows(version))
    return tuple(rows)


def _passing_evidence_by_type(
    evidence_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], tuple[str, ...]]:
    evidence_by_type: dict[str, Mapping[str, Any]] = {}
    failed: list[str] = []
    for row in evidence_rows:
        audit_type = _audit_type(row)
        if not audit_type:
            continue
        if _evidence_passed(row):
            evidence_by_type[audit_type] = row
        else:
            failed.append(audit_type)
    return evidence_by_type, tuple(dict.fromkeys(failed))


def _audit_type(row: Mapping[str, Any]) -> str:
    return str(row.get("audit_type") or row.get("type") or row.get("evidence_type") or "")


def _evidence_passed(row: Mapping[str, Any]) -> bool:
    if "passed" in row:
        return bool(row["passed"])
    status = str(row.get("status") or row.get("verdict") or "").lower()
    if status:
        return status in {"pass", "passed", "ok", "success", "trusted", "approved"}
    return False


def _dependency_is_pinned(dependency: Mapping[str, Any]) -> bool:
    if bool(dependency.get("optional", False)):
        return True
    content_hash = str(
        dependency.get("dependency_content_hash")
        or dependency.get("content_hash")
        or dependency.get("version_hash")
        or ""
    )
    return bool(PINNED_SHA256_RE.fullmatch(content_hash))


def _dependency_label(dependency: Mapping[str, Any]) -> str:
    fqdn = str(
        dependency.get("dependency_artifact_fqdn")
        or dependency.get("fqdn")
        or dependency.get("artifact_fqdn")
        or "<unknown>"
    )
    port_name = str(dependency.get("port_name") or "")
    if port_name:
        return f"{fqdn}:{port_name}"
    return fqdn


def _nested_first(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0].get(key)
    return None


def _as_record_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, Mapping))
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()
