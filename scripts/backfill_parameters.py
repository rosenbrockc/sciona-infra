"""Backfill atom_parameters from audit_manifest.json."""

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


def build_parameter_rows(atom_id: str, atom_entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Map manifest argument details into atom_parameters rows."""
    rows: list[dict[str, Any]] = []
    for position, arg in enumerate(atom_entry.get("argument_details", [])):
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "name": arg["name"],
                "position": position,
                "kind": arg.get("kind", "positional_or_keyword"),
                "type_desc": arg.get("annotation") or "Any",
                "required": arg.get("required", True),
                "default_value_repr": "",
                "technical_description": "",
                "dejargonized_description": "",
                "constraints_json": {},
            }
        )

    next_position = len(rows)
    if atom_entry.get("uses_varargs"):
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "name": "*args",
                "position": next_position,
                "kind": "varargs",
                "type_desc": "Any",
                "required": False,
                "default_value_repr": "",
                "technical_description": "",
                "dejargonized_description": "",
                "constraints_json": {},
            }
        )
        next_position += 1
    if atom_entry.get("uses_kwargs"):
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "name": "**kwargs",
                "position": next_position,
                "kind": "kwargs",
                "type_desc": "Any",
                "required": False,
                "default_value_repr": "",
                "technical_description": "",
                "dejargonized_description": "",
                "constraints_json": {},
            }
        )
    return rows


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
    """Run the parameters backfill."""
    args = parse_args()
    manifest = json.loads(args.audit_manifest.read_text())
    supabase = create_supabase_client()
    atoms_resp = supabase.table("atoms").select("atom_id, fqdn").execute()
    atom_lookup = {row["fqdn"]: row["atom_id"] for row in atoms_resp.data or []}

    stats = {"inserted": 0, "skipped_no_atom": 0, "atoms_processed": 0}

    for atom_entry in manifest.get("atoms", []):
        fqdn = atom_entry["atom_name"]
        atom_id = atom_lookup.get(fqdn)
        if not atom_id:
            log.warning("No atom found for %s", fqdn)
            stats["skipped_no_atom"] += 1
            continue

        stats["atoms_processed"] += 1
        rows = build_parameter_rows(atom_id, atom_entry)
        if not rows:
            continue

        if args.dry_run:
            log.info("DRY RUN would refresh %d parameter rows for %s", len(rows), fqdn)
            stats["inserted"] += len(rows)
            continue

        (
            supabase.table("atom_parameters")
            .delete()
            .eq("atom_id", atom_id)
            .is_("version_id", "null")
            .execute()
        )
        supabase.table("atom_parameters").insert(rows).execute()
        stats["inserted"] += len(rows)

    log.info("Parameters backfill complete: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
