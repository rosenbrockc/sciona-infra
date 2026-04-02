#!/usr/bin/env python3
"""Backfill atom_references from per-atom references.json files."""

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

DEFAULT_ATOMS_ROOT = Path("../ageo-atoms/ageoa")
DEFAULT_REGISTRY_PATH = Path("../ageo-atoms/data/references/registry.json")

MATCH_TYPE_TO_SOURCE = {
    "manual": "manual",
    "ast_subgraph": "llm_extracted",
    "name_heuristic": "llm_extracted",
}


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    """Load the central bibliography keyed by ref_id."""
    data = json.loads(path.read_text())
    refs = data.get("references", data)
    if not isinstance(refs, dict):
        raise ValueError(f"Registry payload at {path} must contain an object")
    return {str(ref_id): dict(entry) for ref_id, entry in refs.items()}


def extract_fqdn(atom_key: str) -> str:
    """Extract the FQDN from a manifest_key or return the raw key for legacy entries."""
    fqdn, _, _rest = atom_key.partition("@")
    return fqdn


def build_ref_key(ref_id: str, registry_entry: dict[str, Any]) -> str:
    """Resolve the atom_references deduplication key."""
    doi = registry_entry.get("doi")
    if doi:
        return str(doi)
    if ref_id:
        return ref_id
    title = str(registry_entry.get("title", "unknown"))
    return title[:80]


def map_source(match_metadata: dict[str, Any]) -> str:
    """Map the per-atom match_type into the atom_references source enum."""
    return MATCH_TYPE_TO_SOURCE.get(str(match_metadata.get("match_type", "")), "manual")


def resolve_atom_id(
    supabase: "Client",
    fqdn: str,
    cache: dict[str, str | None],
) -> str | None:
    """Resolve an atom_id by fqdn with caching."""
    if fqdn in cache:
        return cache[fqdn]
    result = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    atom_id = result.data[0]["atom_id"] if result.data else None
    cache[fqdn] = atom_id
    return atom_id


def build_atom_reference_row(
    atom_id: str,
    ref_id: str,
    registry_entry: dict[str, Any],
    match_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Map a single atom/reference binding into atom_references columns."""
    return {
        "atom_id": atom_id,
        "ref_id": ref_id,
        "ref_key": build_ref_key(ref_id, registry_entry),
        "doi": registry_entry.get("doi"),
        "title": registry_entry.get("title", ""),
        "authors": registry_entry.get("authors", []),
        "year": registry_entry.get("year"),
        "url": registry_entry.get("url", ""),
        "relevance_note": match_metadata.get("notes", ""),
        "confidence": match_metadata.get("confidence", ""),
        "matched_nodes": match_metadata.get("matched_nodes", []),
        "source": map_source(match_metadata),
        "verified": False,
    }


def iter_reference_files(atoms_root: Path) -> list[Path]:
    """List per-atom references.json files while skipping cache directories."""
    return sorted(
        path
        for path in atoms_root.rglob("references.json")
        if "__pycache__" not in path.parts
    )


def backfill_references(
    supabase: "Client",
    atoms_root: Path,
    registry_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill atom_references from references.json bindings."""
    registry = load_registry(registry_path)
    fqdn_cache: dict[str, str | None] = {}
    stats = {
        "inserted": 0,
        "skipped_no_atom": 0,
        "skipped_no_registry": 0,
        "errors": 0,
    }

    refs_files = iter_reference_files(atoms_root)
    log.info("Loaded %d registry entries and found %d references.json files", len(registry), len(refs_files))

    for refs_path in refs_files:
        data = json.loads(refs_path.read_text())
        atoms_block = data.get("atoms", {})
        if not isinstance(atoms_block, dict):
            log.warning("Skipping malformed atoms block in %s", refs_path)
            stats["errors"] += 1
            continue

        for atom_key, atom_data in atoms_block.items():
            fqdn = extract_fqdn(atom_key)
            atom_id = resolve_atom_id(supabase, fqdn, fqdn_cache)
            if not atom_id:
                log.warning("No atom found for %s (from %s)", fqdn, refs_path)
                stats["skipped_no_atom"] += 1
                continue

            references = atom_data.get("references", [])
            if not isinstance(references, list):
                log.warning("Skipping malformed references list for %s in %s", fqdn, refs_path)
                stats["errors"] += 1
                continue

            for ref_binding in references:
                ref_id = str(ref_binding.get("ref_id", "")).strip()
                if not ref_id:
                    log.warning("Empty ref_id for %s in %s", fqdn, refs_path)
                    continue
                registry_entry = registry.get(ref_id)
                if not registry_entry:
                    log.warning("Unknown ref_id %s for %s", ref_id, fqdn)
                    stats["skipped_no_registry"] += 1
                    continue
                row = build_atom_reference_row(
                    atom_id,
                    ref_id,
                    registry_entry,
                    dict(ref_binding.get("match_metadata", {})),
                )
                if dry_run:
                    log.info("DRY RUN: would upsert %s -> %s", fqdn, row["ref_key"])
                    stats["inserted"] += 1
                    continue
                try:
                    (
                        supabase.table("atom_references")
                        .upsert(row, on_conflict="atom_id,ref_key")
                        .execute()
                    )
                    stats["inserted"] += 1
                except Exception:
                    log.exception("Failed to upsert ref %s for atom %s", ref_id, fqdn)
                    stats["errors"] += 1
    return stats


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--atoms-root",
        type=Path,
        default=Path(os.environ.get("ATOM_REFERENCES_ROOT", DEFAULT_ATOMS_ROOT)),
        help="Root of the ageoa source tree containing references.json files.",
    )
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=Path(os.environ.get("REFERENCES_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)),
        help="Path to the canonical references registry JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned upserts without writing to Supabase.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint."""
    from supabase import create_client

    args = parse_args()
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    stats = backfill_references(
        supabase,
        atoms_root=args.atoms_root,
        registry_path=args.registry_path,
        dry_run=args.dry_run,
    )
    log.info("Atom reference backfill complete: %s", stats)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
