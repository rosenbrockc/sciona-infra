"""Report which publication pillars are missing for each atom."""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

log = logging.getLogger(__name__)

PILLAR_NAMES = (
    "io_specs",
    "parameters",
    "dejargonized_description",
    "audit_rollups",
    "references",
)


def create_supabase_client() -> "Client":
    """Create a service-role Supabase client from environment variables."""
    from supabase import create_client

    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(
        os.environ["SUPABASE_URL"],
        service_key,
    )


def compute_missing_pillars(atom_id: str, pillar_sets: dict[str, set[str]]) -> list[str]:
    """Return the missing pillars for a single atom."""
    return [pillar for pillar in PILLAR_NAMES if atom_id not in pillar_sets.get(pillar, set())]


def is_publishable_from_sets(atom_id: str, pillar_sets: dict[str, set[str]]) -> bool:
    """Return whether the atom satisfies all five publication pillars."""
    return not compute_missing_pillars(atom_id, pillar_sets)


def summarize_publication_completeness(
    atoms: dict[str, dict[str, Any]],
    pillar_sets: dict[str, set[str]],
) -> tuple[int, list[tuple[str, list[str]]], int]:
    """Return publishable count, per-atom missing report, and materialized mismatches."""
    publishable_count = 0
    missing_report: list[tuple[str, list[str]]] = []
    mismatch_count = 0

    for atom_id, atom in sorted(atoms.items(), key=lambda item: item[1]["fqdn"]):
        missing = compute_missing_pillars(atom_id, pillar_sets)
        computed_publishable = not missing
        if computed_publishable:
            publishable_count += 1
        else:
            missing_report.append((atom["fqdn"], missing))
        if bool(atom.get("is_publishable")) != computed_publishable:
            mismatch_count += 1

    return publishable_count, missing_report, mismatch_count


def fetch_pillar_sets(supabase: "Client") -> dict[str, set[str]]:
    """Fetch the five publication coverage sets from Supabase."""
    io_specs = supabase.table("atom_io_specs").select("atom_id").execute()
    parameters = supabase.table("atom_parameters").select("atom_id").execute()
    dejarg = (
        supabase.table("atom_descriptions")
        .select("atom_id")
        .eq("kind", "dejargonized")
        .eq("language", "en")
        .lt("jargon_score", 0.4)
        .execute()
    )
    audit_rollups = supabase.table("atom_audit_rollups").select("atom_id").execute()
    references = supabase.table("atom_references").select("atom_id").execute()
    return {
        "io_specs": {row["atom_id"] for row in io_specs.data or []},
        "parameters": {row["atom_id"] for row in parameters.data or []},
        "dejargonized_description": {row["atom_id"] for row in dejarg.data or []},
        "audit_rollups": {row["atom_id"] for row in audit_rollups.data or []},
        "references": {row["atom_id"] for row in references.data or []},
    }


def main() -> int:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    supabase = create_supabase_client()
    atoms_resp = supabase.table("atoms").select("atom_id, fqdn, is_publishable").execute()
    atoms = {row["atom_id"]: row for row in atoms_resp.data or []}
    pillar_sets = fetch_pillar_sets(supabase)

    publishable_count, missing_report, mismatch_count = summarize_publication_completeness(
        atoms,
        pillar_sets,
    )

    print(f"Total atoms: {len(atoms)}")
    print(f"Publishable (all 5 pillars): {publishable_count}")
    print(f"Unpublishable: {len(missing_report)}")
    if missing_report:
        print("\n--- Missing pillars by atom ---")
        for fqdn, missing in missing_report:
            print(f"  {fqdn}: {', '.join(missing)}")
    if mismatch_count:
        print(f"\nWARNING: {mismatch_count} atoms have is_publishable out of sync!")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
