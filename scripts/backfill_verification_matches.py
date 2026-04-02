#!/usr/bin/env python3
"""Backfill atom_verification_matches from matches.json files."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from scripts.backfill_utils import atoms_root_from_env, create_supabase_client_from_env, namespace_from_path, resolve_atom_id

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
VALID_LEVELS = {
    "kernel_proof",
    "type_checked",
    "contract_checked",
    "unverified",
}


def normalize_verification_level(level: str) -> str:
    """Coerce unexpected verification levels to the schema-safe fallback."""
    return level if level in VALID_LEVELS else "unverified"


def build_verification_match_row(atom_id: str, match_result: dict[str, Any]) -> dict[str, Any]:
    """Map a single matches.json entry into an atom_verification_matches row."""
    pdg_node = match_result.get("pdg_node", {})
    verified_match = match_result.get("verified_match") or {}
    candidate = verified_match.get("candidate") or {}
    declaration = candidate.get("declaration") or {}
    return {
        "atom_id": atom_id,
        "version_id": None,
        "predicate_id": pdg_node.get("predicate_id", ""),
        "predicate_statement": pdg_node.get("statement", ""),
        "informal_desc": pdg_node.get("informal_desc", ""),
        "candidate_name": declaration.get("name", ""),
        "candidate_source_lib": declaration.get("source_lib", ""),
        "candidate_score": candidate.get("score"),
        "retrieval_method": candidate.get("retrieval_method", ""),
        "verified": verified_match.get("verified", False),
        "verification_level": normalize_verification_level(
            verified_match.get("verification_level", "unverified")
        ),
        "proof_term": verified_match.get("proof_term", ""),
        "compiler_output": verified_match.get("compiler_output", ""),
        "error_message": verified_match.get("error_message", ""),
        "all_candidates": match_result.get("all_candidates", []),
        "all_verifications": match_result.get("all_verifications", []),
    }


def backfill_verification_matches(
    supabase: Any,
    atoms_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Scan matches.json files and insert rows into atom_verification_matches."""
    stats = {
        "files_found": 0,
        "entries_found": 0,
        "inserted": 0,
        "skipped_no_atom": 0,
        "errors": 0,
    }
    batch: list[dict[str, Any]] = []

    for matches_path in sorted(atoms_root.rglob("matches.json")):
        stats["files_found"] += 1
        try:
            entries = json.loads(matches_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", matches_path, exc)
            stats["errors"] += 1
            continue

        if not isinstance(entries, list):
            logger.warning("Expected array in %s, got %s", matches_path, type(entries).__name__)
            stats["errors"] += 1
            continue

        namespace = namespace_from_path(matches_path)
        for entry in entries:
            stats["entries_found"] += 1
            predicate_id = (entry.get("pdg_node") or {}).get("predicate_id", "")
            if not predicate_id:
                logger.warning("Missing predicate_id in %s", matches_path)
                stats["errors"] += 1
                continue

            atom_id = resolve_atom_id(supabase, namespace, predicate_id)
            if not atom_id:
                logger.warning("Atom not found for %s.%s (%s)", namespace, predicate_id, matches_path)
                stats["skipped_no_atom"] += 1
                continue

            row = build_verification_match_row(atom_id, entry)
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    supabase.table("atom_verification_matches").insert(batch).execute()
                stats["inserted"] += len(batch)
                batch = []

    if batch:
        if not dry_run:
            supabase.table("atom_verification_matches").insert(batch).execute()
        stats["inserted"] += len(batch)

    return stats


def parse_args() -> argparse.Namespace:
    """Parse command-line flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Build rows and log stats without inserting")
    parser.add_argument(
        "--atoms-root",
        type=Path,
        default=atoms_root_from_env(),
        help="Root directory that contains ageoa/**/matches.json",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    supabase = create_supabase_client_from_env()
    result = backfill_verification_matches(supabase, args.atoms_root, dry_run=args.dry_run)
    logger.info("Verification matches backfill complete: %s", result)


if __name__ == "__main__":
    main()
