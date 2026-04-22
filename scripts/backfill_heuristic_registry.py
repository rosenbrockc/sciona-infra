"""Backfill ``heuristic_registry`` from heuristic_metadata.json and family registry."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_METADATA_PATH = Path(
    "../sciona-atoms-ml/src/sciona/atoms/ml/model_selection/heuristic_metadata.json"
)
DEFAULT_FAMILY_PATH = Path(
    "../sciona-atoms-ml/data/heuristics/families/model_selection.json"
)


def build_registry_rows(
    metadata_path: Path,
    family_path: Path,
) -> list[dict[str, Any]]:
    """Build heuristic_registry rows from metadata + family JSON."""
    metadata = json.loads(metadata_path.read_text())
    family_registry = json.loads(family_path.read_text())

    bindings_by_id: dict[str, dict[str, Any]] = {
        b["heuristic_id"]: b
        for b in family_registry.get("heuristic_bindings", [])
    }

    rows: list[dict[str, Any]] = []
    for record in metadata.get("records", []):
        atom_fqdn = record.get("atom_fqdn", "")
        for output in record.get("heuristic_outputs", []):
            h = output.get("heuristic", {})
            heuristic_id = h.get("heuristic_id", "")
            if not heuristic_id:
                continue
            binding = bindings_by_id.get(heuristic_id, {})
            rows.append({
                "heuristic_id": heuristic_id,
                "display_name": h.get("display_name", heuristic_id),
                "dejargonized_meaning": h.get("dejargonized_meaning", ""),
                "evidence_type": h.get("evidence_type", "structured_summary"),
                "value_kind": h.get("value_kind", ""),
                "value_shape": h.get("value_shape", ""),
                "confidence": h.get("confidence", 1.0),
                "producer_kind": h.get("producer_kind", "atom_output"),
                "applicability_scope": h.get("applicability_scope", "cross_family"),
                "supported_action_classes": h.get("supported_action_classes", []),
                "provenance_requirements": h.get("provenance_requirements", []),
                "domain": "ml_model_selection",
                "family": family_registry.get("family", "model_selection"),
                "source_atom_fqdn": atom_fqdn,
                "uncertainty_notes": binding.get("uncertainty_notes", []),
                "references": json.dumps(record.get("references", [])),
            })

    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=Path(os.environ.get("HEURISTIC_METADATA_PATH", DEFAULT_METADATA_PATH)),
    )
    parser.add_argument(
        "--family-path",
        type=Path,
        default=Path(os.environ.get("HEURISTIC_FAMILY_PATH", DEFAULT_FAMILY_PATH)),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    from supabase import create_client

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    rows = build_registry_rows(args.metadata_path, args.family_path)
    log.info("Built %d heuristic registry rows", len(rows))

    if args.dry_run:
        for r in rows:
            log.info("  %s → %s", r["heuristic_id"], r["source_atom_fqdn"])
        return

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    supabase.table("heuristic_registry").upsert(
        rows, on_conflict="heuristic_id"
    ).execute()
    log.info("Upserted %d rows into heuristic_registry", len(rows))


if __name__ == "__main__":
    main()
