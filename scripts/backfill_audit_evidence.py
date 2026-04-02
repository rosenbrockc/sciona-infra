"""Backfill ``atom_audit_evidence`` from ``audit_manifest.json``."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

log = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = Path("../ageo-atoms/data/audit_manifest.json")
DEFAULT_BATCH_SIZE = 50
DEFAULT_RUNNER_VERSION = "backfill-v1"


def build_evidence_rows(
    atom_id: str,
    entry: dict[str, Any],
    *,
    runner_version: str = DEFAULT_RUNNER_VERSION,
) -> list[dict[str, Any]]:
    """Build the synthetic evidence rows for a single manifest atom entry."""
    rows: list[dict[str, Any]] = []
    common = {
        "atom_id": atom_id,
        "source_kind": "automated",
        "runner_version": runner_version,
        "source_revision": entry.get("source_revision") or "",
        "upstream_version": entry.get("upstream_version") or "",
    }

    structural = entry.get("structural_status")
    if structural is not None:
        rows.append(
            {
                **common,
                "audit_type": "structural_audit",
                "passed": structural == "pass",
                "details": {
                    "status": structural,
                    "findings": entry.get("structural_findings", []),
                    "finding_details": entry.get("structural_finding_details", []),
                },
            }
        )

    semantic = entry.get("semantic_status")
    if semantic not in (None, "unknown"):
        rows.append(
            {
                **common,
                "audit_type": "semantic_audit",
                "passed": semantic == "pass",
                "details": {
                    "status": semantic,
                    "findings": entry.get("semantic_findings", []),
                    "finding_details": entry.get("semantic_finding_details", []),
                },
            }
        )

    risk_tier = entry.get("risk_tier")
    if risk_tier is not None:
        rows.append(
            {
                **common,
                "audit_type": "risk_assessment",
                "passed": risk_tier == "low",
                "details": {
                    "risk_tier": risk_tier,
                    "risk_score": entry.get("risk_score", 0),
                    "risk_dimensions": entry.get("risk_dimensions", {}),
                    "risk_reasons": entry.get("risk_reasons", []),
                },
            }
        )

    parity = entry.get("parity_coverage_level")
    if parity not in (None, "unknown", "none"):
        rows.append(
            {
                **common,
                "audit_type": "parity_check",
                "passed": parity in ("positive_and_negative", "parity_or_usage_equivalent"),
                "details": {
                    "coverage_level": parity,
                    "coverage_reasons": entry.get("parity_coverage_reasons", []),
                    "test_status": entry.get("parity_test_status", "unknown"),
                    "fixture_count": entry.get("parity_fixture_count", 0),
                    "case_count": entry.get("parity_case_count", 0),
                    "usage_test_coverage": entry.get("usage_test_coverage", ""),
                },
            }
        )

    runtime = entry.get("runtime_status")
    if runtime not in (None, "not_applicable"):
        rows.append(
            {
                **common,
                "audit_type": "smoke_test",
                "passed": runtime == "pass",
                "details": {
                    "status": runtime,
                    "status_basis": (entry.get("status_basis") or {}).get("runtime", []),
                },
            }
        )

    return rows


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Load the audit manifest and return its atom entries."""
    payload = json.loads(path.read_text())
    atoms = payload.get("atoms", [])
    if not isinstance(atoms, list):
        raise ValueError(f"Expected manifest atoms list in {path}")
    return atoms


def fetch_atom_lookup(supabase: "Client") -> dict[str, str]:
    """Fetch the current ``fqdn -> atom_id`` map from Supabase."""
    response = supabase.table("atoms").select("atom_id, fqdn").execute()
    return {row["fqdn"]: row["atom_id"] for row in response.data or []}


def fetch_existing_backfill_keys(
    supabase: "Client",
    *,
    runner_version: str,
) -> set[tuple[str, str]]:
    """Fetch existing backfill evidence keys so reruns remain pragmatic."""
    response = (
        supabase.table("atom_audit_evidence")
        .select("atom_id, audit_type")
        .eq("runner_version", runner_version)
        .execute()
    )
    return {
        (row["atom_id"], row["audit_type"])
        for row in (response.data or [])
        if row.get("atom_id") and row.get("audit_type")
    }


def backfill_audit_evidence(
    supabase: "Client",
    *,
    manifest_path: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    runner_version: str = DEFAULT_RUNNER_VERSION,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill audit evidence rows from the manifest."""
    manifest_atoms = load_manifest(manifest_path)
    atom_lookup = fetch_atom_lookup(supabase)
    existing_keys = fetch_existing_backfill_keys(supabase, runner_version=runner_version)

    stats = {
        "manifest_atoms": len(manifest_atoms),
        "inserted": 0,
        "skipped_no_atom": 0,
        "skipped_existing": 0,
    }
    batch: list[dict[str, Any]] = []

    for entry in manifest_atoms:
        fqdn = str(entry.get("atom_name", "") or "")
        atom_id = atom_lookup.get(fqdn)
        if atom_id is None:
            stats["skipped_no_atom"] += 1
            continue

        for row in build_evidence_rows(atom_id, entry, runner_version=runner_version):
            key = (row["atom_id"], row["audit_type"])
            if key in existing_keys:
                stats["skipped_existing"] += 1
                continue
            batch.append(row)
            existing_keys.add(key)

            if len(batch) >= batch_size:
                if not dry_run:
                    supabase.table("atom_audit_evidence").insert(batch).execute()
                stats["inserted"] += len(batch)
                batch = []

    if batch:
        if not dry_run:
            supabase.table("atom_audit_evidence").insert(batch).execute()
        stats["inserted"] += len(batch)

    return stats


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=Path(os.environ.get("AUDIT_MANIFEST_PATH", DEFAULT_MANIFEST_PATH)),
        help="Path to audit_manifest.json",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("AUDIT_BACKFILL_BATCH_SIZE", DEFAULT_BATCH_SIZE)),
        help="Insert batch size",
    )
    parser.add_argument(
        "--runner-version",
        default=os.environ.get("AUDIT_BACKFILL_RUNNER_VERSION", DEFAULT_RUNNER_VERSION),
        help="Synthetic runner_version tag used for idempotent reruns",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build rows without inserting them")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    from supabase import create_client

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    stats = backfill_audit_evidence(
        supabase,
        manifest_path=args.manifest_path,
        batch_size=args.batch_size,
        runner_version=args.runner_version,
        dry_run=args.dry_run,
    )
    log.info("Audit evidence backfill complete: %s", stats)


if __name__ == "__main__":
    main()
