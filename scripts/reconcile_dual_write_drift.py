"""Report primary-key drift between legacy PG and Supabase PG for one table."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone


async def _fetch_keys(conn, table: str, pk_column: str, limit: int) -> set[str]:
    rows = await conn.fetch(
        f"SELECT {pk_column}::text AS key FROM public.{table} ORDER BY {pk_column} LIMIT $1",
        limit,
    )
    return {str(row["key"]) for row in rows}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument("--pk-column", required=True)
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()

    import asyncpg

    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    try:
        old_keys = await _fetch_keys(old_pg, args.table, args.pk_column, args.limit)
        supa_keys = await _fetch_keys(supa_pg, args.table, args.pk_column, args.limit)

        payload = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "table": args.table,
            "pk_column": args.pk_column,
            "old_only": sorted(old_keys - supa_keys),
            "supabase_only": sorted(supa_keys - old_keys),
        }
        print(json.dumps(payload, indent=2))
        return 1 if payload["old_only"] or payload["supabase_only"] else 0
    finally:
        await old_pg.close()
        await supa_pg.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
