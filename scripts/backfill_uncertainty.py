#!/usr/bin/env python3
"""Backfill atom_uncertainty_estimates from uncertainty.json files."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from scripts.backfill_utils import atoms_root_from_env, create_supabase_client_from_env, namespace_from_path, resolve_atom_id

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def build_uncertainty_rows(atom_id: str, estimates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map a file's estimates array into insert rows."""
    rows: list[dict[str, Any]] = []
    for estimate in estimates:
        rows.append(
            {
                "atom_id": atom_id,
                "version_id": None,
                "mode": estimate.get("mode", "empirical"),
                "scalar_factor": estimate["scalar_factor"],
                "confidence": estimate["confidence"],
                "n_trials": estimate.get("n_trials", 0),
                "epsilon": estimate.get("epsilon", 0),
                "input_regime": estimate.get("input_regime", ""),
                "notes": estimate.get("notes", ""),
            }
        )
    return rows


def backfill_uncertainty(supabase: Any, atoms_root: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Scan uncertainty.json files and insert rows into atom_uncertainty_estimates."""
    stats = {"found": 0, "inserted": 0, "skipped_no_atom": 0, "errors": 0}
    batch: list[dict[str, Any]] = []

    for uncertainty_path in sorted(atoms_root.rglob("uncertainty.json")):
        stats["found"] += 1
        try:
            payload = json.loads(uncertainty_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", uncertainty_path, exc)
            stats["errors"] += 1
            continue

        atom_name = payload.get("atom", "")
        if not atom_name:
            logger.warning("No atom name in %s", uncertainty_path)
            stats["errors"] += 1
            continue

        namespace = namespace_from_path(uncertainty_path)
        atom_id = resolve_atom_id(supabase, namespace, atom_name)
        if not atom_id:
            logger.warning("Atom not found for %s.%s (%s)", namespace, atom_name, uncertainty_path)
            stats["skipped_no_atom"] += 1
            continue

        try:
            rows = build_uncertainty_rows(atom_id, payload.get("estimates", []))
        except KeyError as exc:
            logger.warning("Malformed uncertainty estimate in %s: missing %s", uncertainty_path, exc)
            stats["errors"] += 1
            continue

        batch.extend(rows)
        if len(batch) >= BATCH_SIZE:
            if not dry_run:
                supabase.table("atom_uncertainty_estimates").insert(batch).execute()
            stats["inserted"] += len(batch)
            batch = []

    if batch:
        if not dry_run:
            supabase.table("atom_uncertainty_estimates").insert(batch).execute()
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
        help="Root directory that contains ageoa/**/uncertainty.json",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    supabase = create_supabase_client_from_env()
    result = backfill_uncertainty(supabase, args.atoms_root, dry_run=args.dry_run)
    logger.info("Uncertainty backfill complete: %s", result)


if __name__ == "__main__":
    main()
