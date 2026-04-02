"""Compare content checksums between legacy PG and Supabase PG."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

TABLES: dict[str, list[str]] = {
    "users": ["user_id"],
    "atoms": ["atom_id"],
    "atom_versions": ["version_id"],
    "hyperparams": ["hp_id"],
    "bounties": ["bounty_id"],
    "submissions": ["submission_id"],
    "atom_audit_rollups": ["atom_id"],
}


async def _content_checksum(conn, table: str, pk_columns: list[str]) -> str:
    order_by = ", ".join(f"t.{col}" for col in pk_columns)
    query = f"""
        SELECT COALESCE(
            md5(
                string_agg(
                    md5(row_to_json(t)::text),
                    ',' ORDER BY {order_by}
                )
            ),
            md5('')
        )
        FROM public.{table} AS t
    """
    value = await conn.fetchval(query)
    return str(value or "")


async def main() -> int:
    import asyncpg

    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    try:
        mismatches: list[dict[str, object]] = []
        for table, pk_columns in TABLES.items():
            old_checksum = await _content_checksum(old_pg, table, pk_columns)
            supa_checksum = await _content_checksum(supa_pg, table, pk_columns)
            if old_checksum != supa_checksum:
                mismatches.append(
                    {
                        "table": table,
                        "pk_columns": pk_columns,
                        "old_pg_checksum": old_checksum,
                        "supabase_checksum": supa_checksum,
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
