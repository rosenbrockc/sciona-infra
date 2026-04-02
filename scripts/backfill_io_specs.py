"""Backfill atom_io_specs from CDG JSON files."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_ATOMS_ROOT = "../ageo-atoms/ageoa"
DEFAULT_AUDIT_MANIFEST_PATH = "../ageo-atoms/data/audit_manifest.json"


def create_supabase_client():
    """Create a service-role Supabase client lazily."""
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("supabase-py is required to run this script") from exc

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def derive_atom_fqdn(cdg_path: Path, atoms_root: Path, node_name: str) -> str:
    """Derive `ageoa.<path>.<node_name>` from a CDG file path."""
    rel_parts = cdg_path.parent.relative_to(atoms_root).parts
    return ".".join((atoms_root.name, *rel_parts, node_name))


def load_manifest_argument_names(path: Path) -> dict[str, list[str]]:
    """Load `atom_name -> argument_names` from audit_manifest.json."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    return {
        entry["atom_name"]: list(entry.get("argument_names", []))
        for entry in payload.get("atoms", [])
        if "atom_name" in entry
    }


def build_io_spec_rows(atom_id: str, node: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a single CDG atomic node to input/output IO spec rows."""
    rows: list[dict[str, Any]] = []
    for ordinal, spec in enumerate(node.get("inputs", [])):
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "direction": "input",
                "name": spec["name"],
                "type_desc": spec.get("type_desc") or "Any",
                "constraints": spec.get("constraints") or "",
                "required": True,
                "default_value_repr": "",
                "ordinal": ordinal,
            }
        )
    for ordinal, spec in enumerate(node.get("outputs", [])):
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "direction": "output",
                "name": spec["name"],
                "type_desc": spec.get("type_desc") or "Any",
                "constraints": spec.get("constraints") or "",
                "required": True,
                "default_value_repr": "",
                "ordinal": ordinal,
            }
        )
    return rows


def input_name_mismatch(cdg_input_names: list[str], manifest_arg_names: list[str]) -> bool:
    """Return whether cross-validation should warn."""
    return bool(manifest_arg_names) and cdg_input_names != manifest_arg_names


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log intended writes without mutating Supabase")
    parser.add_argument(
        "--atoms-root",
        type=Path,
        default=Path(os.environ.get("AGEOA_ATOMS_ROOT", DEFAULT_ATOMS_ROOT)),
        help="Path to the external ageoa source tree",
    )
    parser.add_argument(
        "--audit-manifest",
        type=Path,
        default=Path(os.environ.get("AUDIT_MANIFEST_PATH", DEFAULT_AUDIT_MANIFEST_PATH)),
        help="Path to audit_manifest.json for cross-validation",
    )
    return parser.parse_args()


def main() -> None:
    """Run the IO specs backfill."""
    args = parse_args()
    supabase = create_supabase_client()

    atoms_resp = supabase.table("atoms").select("atom_id, fqdn").execute()
    atom_lookup = {row["fqdn"]: row["atom_id"] for row in atoms_resp.data or []}
    manifest_args = load_manifest_argument_names(args.audit_manifest)

    stats = {"inserted": 0, "skipped_no_atom": 0, "cdg_files": 0, "cross_val_warnings": 0}

    for cdg_path in sorted(args.atoms_root.rglob("cdg.json")):
        stats["cdg_files"] += 1
        cdg = json.loads(cdg_path.read_text())
        for node in cdg.get("nodes", []):
            if node.get("status") != "atomic":
                continue

            node_name = str(node.get("name", ""))
            atom_fqdn = derive_atom_fqdn(cdg_path, args.atoms_root, node_name)
            atom_id = atom_lookup.get(atom_fqdn)
            if not atom_id:
                log.warning("No atom found for %s (CDG %s)", atom_fqdn, cdg_path)
                stats["skipped_no_atom"] += 1
                continue

            cdg_input_names = [spec["name"] for spec in node.get("inputs", [])]
            manifest_arg_names = manifest_args.get(atom_fqdn, [])
            if input_name_mismatch(cdg_input_names, manifest_arg_names):
                log.warning(
                    "Input name mismatch for %s: CDG=%s manifest=%s",
                    atom_fqdn,
                    cdg_input_names,
                    manifest_arg_names,
                )
                stats["cross_val_warnings"] += 1

            rows = build_io_spec_rows(atom_id, node)
            if not rows:
                continue

            if args.dry_run:
                log.info("DRY RUN would refresh %d IO rows for %s", len(rows), atom_fqdn)
                stats["inserted"] += len(rows)
                continue

            # The phase plan stores NULL version_id for initial backfill, which does
            # not deduplicate cleanly under a UNIQUE constraint. Delete/reinsert keeps
            # this backfill idempotent while preserving the planned row shape.
            (
                supabase.table("atom_io_specs")
                .delete()
                .eq("atom_id", atom_id)
                .is_("version_id", "null")
                .execute()
            )
            supabase.table("atom_io_specs").insert(rows).execute()
            stats["inserted"] += len(rows)

    log.info("IO specs backfill complete: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
