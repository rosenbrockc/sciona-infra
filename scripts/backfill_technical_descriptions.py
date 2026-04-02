"""Backfill technical atom_descriptions from audit_manifest.json and atoms.description."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_AUDIT_MANIFEST_PATH = "../ageo-atoms/data/audit_manifest.json"


def create_supabase_client():
    """Create a service-role Supabase client lazily."""
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("supabase-py is required to run this script") from exc

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def choose_technical_content(atom_entry: dict[str, Any], atom_row: dict[str, Any]) -> str:
    """Prefer docstring_summary over the existing atoms.description fallback."""
    return str(atom_entry.get("docstring_summary") or atom_row.get("description") or "").strip()


def build_description_row(atom_id: str, content: str) -> dict[str, Any]:
    """Build a technical description row."""
    return {
        "atom_id": atom_id,
        "kind": "technical",
        "language": "en",
        "content": content,
        "generated_by": "backfill-v1",
        "reviewed": False,
        "jargon_score": 1.0,
    }


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log intended writes without mutating Supabase")
    parser.add_argument(
        "--audit-manifest",
        type=Path,
        default=Path(os.environ.get("AUDIT_MANIFEST_PATH", DEFAULT_AUDIT_MANIFEST_PATH)),
        help="Path to audit_manifest.json",
    )
    return parser.parse_args()


def main() -> None:
    """Run the technical descriptions backfill."""
    args = parse_args()
    manifest = json.loads(args.audit_manifest.read_text())
    supabase = create_supabase_client()
    atoms_resp = supabase.table("atoms").select("atom_id, fqdn, description").execute()
    atom_lookup = {row["fqdn"]: row for row in atoms_resp.data or []}

    stats = {"inserted": 0, "skipped_no_content": 0, "skipped_no_atom": 0}
    rows: list[dict[str, Any]] = []

    for atom_entry in manifest.get("atoms", []):
        fqdn = atom_entry["atom_name"]
        atom_row = atom_lookup.get(fqdn)
        if not atom_row:
            stats["skipped_no_atom"] += 1
            continue

        content = choose_technical_content(atom_entry, atom_row)
        if not content:
            stats["skipped_no_content"] += 1
            continue
        rows.append(build_description_row(atom_row["atom_id"], content))

    for batch_start in range(0, len(rows), 100):
        batch = rows[batch_start : batch_start + 100]
        if args.dry_run:
            log.info("DRY RUN would upsert %d technical descriptions", len(batch))
            stats["inserted"] += len(batch)
            continue
        supabase.table("atom_descriptions").upsert(batch, on_conflict="atom_id,kind,language").execute()
        stats["inserted"] += len(batch)

    log.info("Technical descriptions backfill complete: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
