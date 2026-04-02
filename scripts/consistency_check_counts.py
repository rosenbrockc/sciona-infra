"""Compare row counts between legacy PG and Supabase PG."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

TABLES = [
    "users",
    "atoms",
    "atom_versions",
    "atom_authors",
    "hyperparams",
    "atom_benchmarks",
    "bounties",
    "submissions",
    "verification_runs",
    "fuzz_results",
    "atom_io_specs",
    "atom_parameters",
    "atom_descriptions",
    "atom_references",
    "atom_audit_evidence",
    "atom_audit_rollups",
    "atom_uncertainty_estimates",
    "atom_verification_matches",
    "roles",
    "user_role_assignments",
    "organizations",
    "organization_memberships",
    "user_memberships",
    "user_entitlement_grants",
]


async def _table_count(conn, table: str) -> int:
    value = await conn.fetchval(f"SELECT COUNT(*) FROM public.{table}")
    return int(value or 0)


async def main() -> int:
    import asyncpg

    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    try:
        mismatches: list[dict[str, object]] = []
        for table in TABLES:
            old_count = await _table_count(old_pg, table)
            supa_count = await _table_count(supa_pg, table)
            if old_count != supa_count:
                mismatches.append(
                    {
                        "table": table,
                        "old_pg_count": old_count,
                        "supabase_count": supa_count,
                    }
                )

        payload = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "table_count": len(TABLES),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }
        print(json.dumps(payload, indent=2))
        return 1 if mismatches else 0
    finally:
        await old_pg.close()
        await supa_pg.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
