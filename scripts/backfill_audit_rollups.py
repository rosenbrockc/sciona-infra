"""Backfill ``atom_audit_rollups`` from ``audit_manifest.json``."""

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


def build_rollup_row(atom_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Map a manifest atom entry to a single audit rollup row."""
    return {
        "atom_id": atom_id,
        "overall_verdict": entry.get("overall_verdict") or "unknown",
        "structural_status": entry.get("structural_status") or "unknown",
        "runtime_status": entry.get("runtime_status") or "unknown",
        "semantic_status": entry.get("semantic_status") or "unknown",
        "developer_semantics_status": entry.get("developer_semantics_status") or "unknown",
        "risk_tier": entry.get("risk_tier") or "medium",
        "risk_score": entry.get("risk_score", 0),
        "risk_dimensions": entry.get("risk_dimensions") or {},
        "risk_reasons": entry.get("risk_reasons") or [],
        "acceptability_score": entry.get("acceptability_score", 0),
        "acceptability_band": entry.get("acceptability_band") or "unknown",
        "parity_coverage_level": entry.get("parity_coverage_level") or "unknown",
        "parity_test_status": entry.get("parity_test_status") or "unknown",
        "parity_fixture_count": entry.get("parity_fixture_count", 0),
        "parity_case_count": entry.get("parity_case_count", 0),
        "review_status": entry.get("review_status") or "missing",
        "review_semantic_verdict": entry.get("review_semantic_verdict") or "unknown",
        "review_developer_semantics_verdict": entry.get("review_developer_semantics_verdict") or "unknown",
        "review_limitations": entry.get("review_limitations") or [],
        "review_required_actions": entry.get("review_required_actions") or [],
        "trust_readiness": entry.get("trust_readiness") or "not_ready",
        "trust_blockers": entry.get("trust_blockers") or [],
    }


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


def backfill_audit_rollups(
    supabase: "Client",
    *,
    manifest_path: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill audit rollup rows from the manifest."""
    manifest_atoms = load_manifest(manifest_path)
    atom_lookup = fetch_atom_lookup(supabase)

    stats = {
        "manifest_atoms": len(manifest_atoms),
        "upserted": 0,
        "skipped_no_atom": 0,
    }
    batch: list[dict[str, Any]] = []

    for entry in manifest_atoms:
        fqdn = str(entry.get("atom_name", "") or "")
        atom_id = atom_lookup.get(fqdn)
        if atom_id is None:
            stats["skipped_no_atom"] += 1
            continue

        batch.append(build_rollup_row(atom_id, entry))
        if len(batch) >= batch_size:
            if not dry_run:
                supabase.table("atom_audit_rollups").upsert(batch, on_conflict="atom_id").execute()
            stats["upserted"] += len(batch)
            batch = []

    if batch:
        if not dry_run:
            supabase.table("atom_audit_rollups").upsert(batch, on_conflict="atom_id").execute()
        stats["upserted"] += len(batch)

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
        default=int(os.environ.get("AUDIT_ROLLUP_BATCH_SIZE", DEFAULT_BATCH_SIZE)),
        help="Upsert batch size",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build rows without upserting them")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    from supabase import create_client

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    stats = backfill_audit_rollups(
        supabase,
        manifest_path=args.manifest_path,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    log.info("Audit rollup backfill complete: %s", stats)


if __name__ == "__main__":
    main()
