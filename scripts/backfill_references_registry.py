#!/usr/bin/env python3
"""Backfill the references_registry table from the canonical JSON registry."""

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

DEFAULT_REGISTRY_PATH = Path("../ageo-atoms/data/references/registry.json")


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    """Load the canonical bibliography keyed by ref_id."""
    data = json.loads(path.read_text())
    refs = data.get("references", data)
    if not isinstance(refs, dict):
        raise ValueError(f"Registry payload at {path} must contain an object")
    return {str(ref_id): dict(entry) for ref_id, entry in refs.items()}


def build_registry_row(ref_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Map a registry JSON entry to references_registry columns."""
    return {
        "ref_id": ref_id,
        "ref_type": entry.get("type", "paper"),
        "title": entry.get("title", ""),
        "authors": entry.get("authors", []),
        "year": entry.get("year"),
        "venue": entry.get("venue", ""),
        "doi": entry.get("doi"),
        "url": entry.get("url", ""),
        "bibtex_key": entry.get("bibtex_key", ref_id),
        "bibtex_raw": entry.get("bibtex_raw", ""),
    }


def backfill_registry(
    supabase: "Client",
    registry_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Backfill the registry table and return summary stats."""
    registry = load_registry(registry_path)
    log.info("Loaded %d registry entries from %s", len(registry), registry_path)

    stats = {"upserted": 0, "errors": 0}
    for ref_id, entry in sorted(registry.items()):
        row = build_registry_row(ref_id, entry)
        if dry_run:
            log.info("DRY RUN: would upsert registry entry %s", ref_id)
            stats["upserted"] += 1
            continue
        try:
            (
                supabase.table("references_registry")
                .upsert(row, on_conflict="ref_id")
                .execute()
            )
            stats["upserted"] += 1
        except Exception:
            log.exception("Failed to upsert registry entry %s", ref_id)
            stats["errors"] += 1
    return stats


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
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
    stats = backfill_registry(supabase, args.registry_path, dry_run=args.dry_run)
    log.info("Registry backfill complete: %s", stats)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
