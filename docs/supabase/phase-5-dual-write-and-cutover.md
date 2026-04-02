# Phase 5: Dual-Write Migration & Full Cutover

## Overview

Phase 5 is the final migration phase. It transitions the production system from
the old asyncpg-backed PostgreSQL database to Supabase as the sole data store.
The phase is split into three stages with independent go/no-go gates:

1. **Dual-Write** -- every mutating API operation writes to both old PG and
   Supabase. Reads remain on old PG. Nightly consistency checks detect
   divergence.
2. **Read Cutover** -- the read path switches to Supabase behind a feature flag.
   Old PG stays online as a read-only fallback.
3. **Full Cutover** -- dual-write code is removed, old PG is decommissioned, and
   legacy auth env vars (`SCIONA_JWT_PUBLIC_KEY`, `SCIONA_JWT_PRIVATE_KEY`) are
   deleted.

When Phase 5 is complete:

- Supabase is the sole database for all API reads and writes.
- `sciona catalog sync`, `load_hyperparams_manifest_sqlite()`, and
  `load_benchmarks_sqlite()` generate their SQLite caches from Supabase.
- The `/catalog/manifest` HTTP endpoint is removed.
- `asyncpg` and `PyJWT` are removed from production dependencies.
- No `SCIONA_JWT_PUBLIC_KEY` / `SCIONA_JWT_PRIVATE_KEY` env vars remain.

---

## Dependencies

| Dependency | Why |
|---|---|
| Phase 4 complete (client code supports Supabase) | All routers must already have Supabase-compatible query implementations before dual-write can begin. |
| Supabase schema + RLS policies deployed | The schema from the migration plan (Section 2) must be live with all RLS policies and triggers active. |
| Data migration complete (Phase 2 backfill) | All historical data must already exist in Supabase so consistency checks have a valid baseline. |
| `snapshot.py` rewritten | The SQLite manifest generator must be able to produce manifests from Supabase before the read cutover. |
| Monitoring infrastructure | Datadog/Grafana dashboards and PagerDuty alerts for Supabase latency, error rates, and consistency-check failures. |

---

## Stage 1: Dual-Write

### 1.1 Implementation Approach

Use a **decorator pattern** on the `get_db` dependency rather than modifying
every router individually. This keeps router code unchanged and centralises the
dual-write logic in one place.

#### DualWriteConnection wrapper

```python
# sciona/api/dual_write.py

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Structured metrics emitter -- replace with your real metrics client.
# During dual-write, every Supabase mirror call is instrumented.
_metrics: dict[str, int] = {
    "dual_write_attempts": 0,
    "dual_write_failures": 0,
    "dual_write_latency_ms_total": 0,
}


def get_dual_write_metrics() -> dict[str, int]:
    """Expose dual-write health metrics for the /healthz endpoint."""
    return dict(_metrics)


class DualWriteConnection:
    """Wraps the primary asyncpg connection and mirrors mutating operations
    to the Supabase client.

    Read operations are forwarded only to the primary connection (old PG).
    Write operations (INSERT, UPDATE, DELETE, and any query whose SQL starts
    with a mutating keyword) are executed on both.
    """

    _MUTATING_PREFIXES = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE")

    def __init__(self, primary_conn: Any, supabase_conn: Any) -> None:
        self._primary = primary_conn
        self._supabase = supabase_conn

    def _is_mutating(self, query: str) -> bool:
        normalised = query.strip().upper()
        return any(normalised.startswith(p) for p in self._MUTATING_PREFIXES)

    async def _mirror_to_supabase(
        self, method: str, query: str, *args: Any, **kwargs: Any
    ) -> Any | None:
        """Attempt the same operation on Supabase. Failures are logged, never
        raised. Returns the Supabase result on success, None on failure."""
        _metrics["dual_write_attempts"] += 1
        t0 = time.monotonic()
        try:
            fn = getattr(self._supabase, method)
            result = await fn(query, *args, **kwargs)
            return result
        except Exception:
            _metrics["dual_write_failures"] += 1
            logger.exception(
                "dual-write: supabase mirror failed for %s | query_prefix=%s",
                method,
                query[:80],
            )
            return None
        finally:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _metrics["dual_write_latency_ms_total"] += elapsed_ms

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> Any:
        """Read-only -- primary only."""
        return await self._primary.fetch(query, *args, **kwargs)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> Any:
        """Reads go to primary. If the query is mutating (INSERT ... RETURNING),
        it also fires on Supabase."""
        result = await self._primary.fetchrow(query, *args, **kwargs)
        if self._is_mutating(query):
            await self._mirror_to_supabase("fetchrow", query, *args, **kwargs)
        return result

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        """Execute on primary, then mirror to Supabase."""
        result = await self._primary.execute(query, *args, **kwargs)
        if self._is_mutating(query):
            await self._mirror_to_supabase("execute", query, *args, **kwargs)
        return result

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        result = await self._primary.fetchval(query, *args, **kwargs)
        if self._is_mutating(query):
            await self._mirror_to_supabase("fetchval", query, *args, **kwargs)
        return result
```

Key design decisions:

- **Primary always executes first.** If the primary fails, neither database is
  written. If Supabase fails, the primary write has already succeeded and the
  error is logged -- the nightly consistency check catches drift.
- **No transactions spanning both databases.** Two-phase commit across
  independent Postgres instances is fragile and unnecessary for a migration
  period. The consistency check scripts reconcile drift.
- **Supabase failures are logged, not raised.** The dual-write period is
  designed so that old PG remains authoritative. A Supabase write failure must
  not break the user-facing request.
- **Instrumented metrics.** Every mirror attempt is counted, timed, and
  surfaced via `get_dual_write_metrics()` so that dashboards and alerting
  can track dual-write health without log parsing.

#### Modified `get_db` dependency

```python
# In sciona/api/deps.py -- modified for dual-write

import os

async def get_db(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Database not available")

    supabase_pool = getattr(request.app.state, "supabase_db_pool", None)
    dual_write_enabled = os.environ.get("SCIONA_DUAL_WRITE", "0") == "1"

    async with pool.acquire() as conn:
        if dual_write_enabled and supabase_pool is not None:
            async with supabase_pool.acquire() as supa_conn:
                yield DualWriteConnection(conn, supa_conn)
        else:
            yield conn
```

#### Lifespan changes in `app.py`

Add a second asyncpg pool for the Supabase direct Postgres connection (not the
PostgREST client -- we need raw SQL parity for the dual-write):

```python
# In _lifespan(), after the primary pool creation:

supabase_postgres_uri = os.environ.get("SCIONA_SUPABASE_POSTGRES_URI", "")
if supabase_postgres_uri:
    try:
        supabase_db_pool = await asyncpg.create_pool(
            supabase_postgres_uri,
            min_size=2,
            max_size=10,
            statement_cache_size=0,  # PgBouncer compat
        )
        app.state.supabase_db_pool = supabase_db_pool
    except Exception:
        logger.exception("Failed to create Supabase pool; dual-write disabled")
        supabase_db_pool = None
```

### 1.2 Mutating Endpoints Covered

All mutating operations flow through `get_db`, so the DualWriteConnection
automatically covers:

| Router | Mutating endpoints |
|---|---|
| `registry.py` | `POST /atoms` (publish_atom) |
| `bounty.py` | `POST /bounties`, `POST /bounties/{id}/fund`, `POST /bounties/{id}/cancel`, `POST /bounties/{id}/submissions`, `POST /bounties/{id}/submissions/{id}/update-target` |
| `verification.py` | `POST /verification/runs` (if present), settlement writes |
| `auth.py` | User upsert during login |

**Audit step before enabling dual-write**: grep all routers for raw SQL that
bypasses `get_db` (e.g., direct pool usage, background tasks). Any such code
must be refactored to use the dependency or wrapped with its own dual-write
logic. Specifically check:

- Background Celery/ARQ tasks that write directly to the pool.
- `snapshot.py` if it writes anything (it should be read-only).
- Any admin scripts that connect to the database directly.

### 1.3 Consistency Check Scripts

Run nightly via cron (or GitHub Actions scheduled workflow). Three scripts
form a layered verification strategy: row counts, PK checksums, and
column-level content checksums.

#### 1.3.1 Row-count comparison

```python
# scripts/consistency_check_counts.py

import asyncio
import asyncpg
import json
import os
import sys
from datetime import datetime, timezone

TABLES = [
    "users", "atoms", "atom_versions", "atom_authors",
    "hyperparams", "atom_benchmarks", "bounties", "submissions",
    "verification_runs", "fuzz_results",
    # New documentation tables
    "atom_io_specs", "atom_parameters", "atom_descriptions",
    "atom_references", "atom_audit_evidence", "atom_audit_rollups",
    "atom_uncertainty_estimates", "atom_verification_matches",
    # Entitlement tables
    "roles", "user_role_assignments", "organizations",
    "organization_memberships", "user_memberships",
    "user_entitlement_grants",
]

async def main() -> int:
    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    failures = 0
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "check": "row_count",
        "tables": {},
    }

    for table in TABLES:
        try:
            old_count = await old_pg.fetchval(f"SELECT COUNT(*) FROM {table}")
        except Exception:
            old_count = None  # table may not exist in old PG (new tables)
        supa_count = await supa_pg.fetchval(f"SELECT COUNT(*) FROM {table}")

        if old_count is None:
            report["tables"][table] = {"status": "skipped", "reason": "not in old PG"}
            continue

        if old_count != supa_count:
            print(f"MISMATCH {table}: old={old_count} supabase={supa_count}")
            report["tables"][table] = {
                "status": "mismatch",
                "old": old_count,
                "supabase": supa_count,
                "delta": supa_count - old_count,
            }
            failures += 1
        else:
            print(f"OK {table}: {old_count} rows")
            report["tables"][table] = {"status": "ok", "count": old_count}

    # Write structured report for downstream alerting
    report_path = f"/tmp/consistency_counts_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to {report_path}")

    await old_pg.close()
    await supa_pg.close()
    return failures

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### 1.3.2 PK checksum comparison

Verifies that both databases contain the same set of primary key values for
each table. Catches missing or extra rows that a simple count might mask
(e.g., one row deleted and one inserted).

```python
# scripts/consistency_check_checksums.py

import asyncio
import asyncpg
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# table -> (pk_column, order_column)
TABLE_PKS = {
    "atoms": ("atom_id", "atom_id"),
    "atom_versions": ("version_id", "version_id"),
    "users": ("user_id", "user_id"),
    "bounties": ("bounty_id", "bounty_id"),
    "submissions": ("submission_id", "submission_id"),
    "atom_authors": ("atom_id || '|' || user_id", "atom_id"),
    "hyperparams": ("hp_id", "hp_id"),
    "atom_io_specs": ("io_spec_id", "io_spec_id"),
    "atom_parameters": ("parameter_id", "parameter_id"),
    "atom_descriptions": ("description_id", "description_id"),
    "atom_references": ("reference_id", "reference_id"),
    "atom_audit_evidence": ("evidence_id", "evidence_id"),
    "atom_audit_rollups": ("atom_id", "atom_id"),
}

async def table_checksum(conn, table: str, pk: str, order: str) -> str:
    rows = await conn.fetch(
        f"SELECT ({pk})::text FROM {table} ORDER BY {order}"
    )
    h = hashlib.sha256()
    for row in rows:
        h.update(row[0].encode())
    return h.hexdigest()

async def main() -> int:
    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    failures = 0
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "check": "pk_checksum",
        "tables": {},
    }

    for table, (pk, order) in TABLE_PKS.items():
        try:
            old_hash = await table_checksum(old_pg, table, pk, order)
        except Exception:
            report["tables"][table] = {"status": "skipped"}
            continue
        supa_hash = await table_checksum(supa_pg, table, pk, order)
        if old_hash != supa_hash:
            print(f"CHECKSUM MISMATCH {table}: old={old_hash[:16]} supa={supa_hash[:16]}")
            report["tables"][table] = {
                "status": "mismatch",
                "old_hash": old_hash[:16],
                "supa_hash": supa_hash[:16],
            }
            failures += 1
        else:
            print(f"OK {table}: {old_hash[:16]}")
            report["tables"][table] = {"status": "ok", "hash": old_hash[:16]}

    report_path = f"/tmp/consistency_checksums_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    await old_pg.close()
    await supa_pg.close()
    return failures

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### 1.3.3 Column-level content checksum

For critical tables, goes beyond PK comparison and checksums actual column
values. This catches cases where a row exists in both databases but has
different content (e.g., a dual-write where the UPDATE hit old PG but the
Supabase mirror saw a stale version).

```python
# scripts/consistency_check_content.py

import asyncio
import asyncpg
import hashlib
import os
import sys

# table -> (columns to hash, pk_column, order_column)
CONTENT_CHECKS = {
    "atoms": (
        ["atom_id", "fqdn", "owner_id", "status", "visibility_tier",
         "is_publishable", "description"],
        "atom_id",
        "atom_id",
    ),
    "users": (
        ["user_id", "github_id", "github_login", "effective_tier",
         "is_blacklisted"],
        "user_id",
        "user_id",
    ),
    "bounties": (
        ["bounty_id", "principal_id", "status", "escrow_amount"],
        "bounty_id",
        "bounty_id",
    ),
}

async def content_checksum(conn, table: str, columns: list[str], pk: str, order: str) -> str:
    cols = ", ".join(f"COALESCE({c}::text, '')" for c in columns)
    rows = await conn.fetch(
        f"SELECT {cols} FROM {table} ORDER BY {order}"
    )
    h = hashlib.sha256()
    for row in rows:
        for val in row.values():
            h.update(str(val).encode())
    return h.hexdigest()

async def main() -> int:
    old_pg = await asyncpg.connect(os.environ["SCIONA_POSTGRES_URI"])
    supa_pg = await asyncpg.connect(os.environ["SCIONA_SUPABASE_POSTGRES_URI"])
    failures = 0

    for table, (columns, pk, order) in CONTENT_CHECKS.items():
        try:
            old_hash = await content_checksum(old_pg, table, columns, pk, order)
        except Exception:
            continue
        supa_hash = await content_checksum(supa_pg, table, columns, pk, order)
        if old_hash != supa_hash:
            print(f"CONTENT MISMATCH {table}: old={old_hash[:16]} supa={supa_hash[:16]}")
            failures += 1
        else:
            print(f"OK {table}: content hash {old_hash[:16]}")

    await old_pg.close()
    await supa_pg.close()
    return failures

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### 1.3.4 Reconciliation script

When divergence is detected, a targeted reconciliation script copies missing
rows from old PG to Supabase. This is always one-directional during
dual-write: old PG is authoritative.

```python
# scripts/reconcile_missing_rows.py
#
# For each table, find PKs present in old PG but absent from Supabase,
# then INSERT them into Supabase. This is a one-directional sync
# (old PG is authoritative during dual-write).
#
# Usage:
#   SCIONA_POSTGRES_URI=... SCIONA_SUPABASE_POSTGRES_URI=... \
#     python scripts/reconcile_missing_rows.py [--table atoms] [--dry-run]
#
# The script:
# 1. Queries PKs from both databases for the target table(s).
# 2. Computes the set difference (in old PG but not in Supabase).
# 3. For each missing PK, SELECT * from old PG and INSERT into Supabase.
# 4. Logs every reconciled row for audit trail.
# 5. In --dry-run mode, reports what would be reconciled without writing.
```

### 1.4 Monitoring During Dual-Write

| Metric | Source | Alert threshold |
|---|---|---|
| Dual-write Supabase failure rate | `get_dual_write_metrics()` exposed via `/healthz` | > 0.1% of requests over 5 minutes |
| Row-count divergence | Nightly `consistency_check_counts.py` | Any table with count mismatch |
| PK checksum divergence | Nightly `consistency_check_checksums.py` | Any table with hash mismatch |
| Content divergence | Nightly `consistency_check_content.py` | Any table with content mismatch |
| Supabase write latency (p99) | `dual_write_latency_ms_total` / `dual_write_attempts` | > 500ms |
| Old PG write latency (p99) | Application timing logs | > 200ms |
| Supabase connection pool errors | asyncpg pool `acquire()` timeout count | Any timeout |
| Reconciliation runs triggered | Reconciliation script execution logs | > 0 in 3 consecutive days |

#### Dashboard setup

Create a Grafana (or Datadog) dashboard with the following panels:

1. **Dual-write success/failure rate** -- time series of `dual_write_attempts`
   vs `dual_write_failures` per minute.
2. **Dual-write latency histogram** -- p50/p95/p99 of Supabase mirror
   latency.
3. **Consistency check results** -- daily table showing pass/fail per table
   from the nightly scripts.
4. **Connection pool utilisation** -- both old PG and Supabase pools showing
   active/idle/waiting connections.

#### PagerDuty integration

- **P2 alert**: Dual-write failure rate exceeds 1% over 15 minutes.
- **P3 alert**: Any nightly consistency check fails.
- **P1 alert**: Dual-write failure rate exceeds 10% (indicates Supabase is
  down or unreachable; not user-facing but needs immediate investigation).

### 1.5 Go/No-Go for Stage 1 Completion

All of the following must be true for at least **7 consecutive days** before
proceeding to Stage 2:

- [ ] Zero checksum mismatches in nightly consistency checks (all three scripts).
- [ ] Dual-write Supabase failure rate below 0.01%.
- [ ] No manual reconciliation needed in the last 3 days.
- [ ] All mutating endpoints covered (verified by audit of access logs: compare
      write counts in old PG vs Supabase per table per day).
- [ ] Supabase write latency p99 < 300ms.
- [ ] No connection pool timeouts or exhaustion events.

### 1.6 Rollback (Stage 1)

Set `SCIONA_DUAL_WRITE=0` (or remove the env var). The DualWriteConnection
stops being created; `get_db` yields the raw asyncpg connection. No data loss --
old PG has every write. Supabase can be re-synced from old PG when the issue is
resolved.

**Rollback steps**:

1. Set `SCIONA_DUAL_WRITE=0` in the deployment manifest.
2. Deploy (or restart the service if using env var hot-reload).
3. Verify in logs that `DualWriteConnection` is no longer instantiated.
4. Optionally remove the `SCIONA_SUPABASE_POSTGRES_URI` env var to reclaim
   the Supabase connection pool.
5. File an incident report documenting what caused the rollback.
6. After the root cause is fixed, re-run the reconciliation script to bring
   Supabase back in sync, then re-enable dual-write.

---

## Stage 2: Read Cutover

### 2.1 Feature Flag Strategy

Use an environment variable feature flag with per-router granularity:

```
SCIONA_READ_SOURCE=pg          # default: all reads from old PG
SCIONA_READ_SOURCE=supabase    # all reads from Supabase
```

For gradual rollout, a per-router override is available:

```
SCIONA_READ_SOURCE_CATALOG=supabase   # catalog reads from Supabase
SCIONA_READ_SOURCE_REGISTRY=pg        # registry reads still from old PG
SCIONA_READ_SOURCE_DASHBOARD=supabase # dashboard reads from Supabase
SCIONA_READ_SOURCE_BOUNTY=pg          # bounty reads still from old PG
SCIONA_READ_SOURCE_VERIFICATION=pg    # verification reads still from old PG
```

#### Implementation in `deps.py`

```python
def _read_source(router_name: str | None = None) -> str:
    """Determine which database to read from.

    Resolution order:
    1. Per-router override: SCIONA_READ_SOURCE_{ROUTER}
    2. Global setting: SCIONA_READ_SOURCE
    3. Default: 'pg'
    """
    if router_name:
        override = os.environ.get(
            f"SCIONA_READ_SOURCE_{router_name.upper()}", ""
        )
        if override:
            return override
    return os.environ.get("SCIONA_READ_SOURCE", "pg")


async def get_read_db(request: Request, router_name: str | None = None) -> Any:
    """Yield a read-only connection from the appropriate database."""
    source = _read_source(router_name)
    if source == "supabase":
        pool = getattr(request.app.state, "supabase_db_pool", None)
        if pool is None:
            raise HTTPException(503, "Supabase not available")
        async with pool.acquire() as conn:
            yield conn
    else:
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            raise HTTPException(503, "Database not available")
        async with pool.acquire() as conn:
            yield conn
```

Routers that only do reads (e.g., `catalog.py`, `dashboard.py`,
`verification.py` GET endpoints) switch their dependency from `get_db` to
`get_read_db`. Mutating routers continue to use `get_db` (which still
dual-writes).

#### Canary request system

To validate reads during the transition, deploy a canary process that
periodically issues the same read query against both databases and compares
results:

```python
# scripts/canary_reads.py
#
# Runs every 5 minutes. For a sample of read queries:
# 1. Execute against old PG.
# 2. Execute against Supabase.
# 3. Compare result sets (row count, column values).
# 4. Report any divergence to the alerting system.
#
# Canary queries:
# - SELECT * FROM atoms WHERE status = 'approved' ORDER BY fqdn LIMIT 50
# - SELECT * FROM users ORDER BY user_id LIMIT 50
# - SELECT * FROM bounties WHERE status = 'open' ORDER BY created_at DESC LIMIT 20
# - SELECT COUNT(*) FROM atom_audit_rollups WHERE overall_verdict = 'trusted'
# - RPC call: get_atom_document('some_known_fqdn')
```

### 2.2 Cutover Sequence

The read cutover is done incrementally, one router at a time, with a
monitoring soak period between each switch.

| Step | Action | Soak period |
|---|---|---|
| 1 | `SCIONA_READ_SOURCE_CATALOG=supabase` | 24 hours |
| 2 | `SCIONA_READ_SOURCE_DASHBOARD=supabase` | 24 hours |
| 3 | `SCIONA_READ_SOURCE_BOUNTY=supabase` | 24 hours |
| 4 | `SCIONA_READ_SOURCE_VERIFICATION=supabase` | 24 hours |
| 5 | `SCIONA_READ_SOURCE=supabase` (all remaining) | 48 hours |
| 6 | Set old PG to read-only: `ALTER DATABASE sciona SET default_transaction_read_only = on;` | -- |

**Rationale for ordering**: Catalog is the highest-traffic read path and
read-only, making it the lowest-risk first candidate. Dashboard is next
because it is non-critical for pipeline operations. Bounty and verification
are switched later because they have tighter correctness requirements (e.g.,
settlement calculations).

### 2.3 Old PG as Read-Only Fallback

During Stage 2, old PG stays online and receives dual-writes. If Supabase read
latency degrades or error rates spike, flip `SCIONA_READ_SOURCE=pg` to restore
the old read path instantly. The dual-write ensures old PG data is still current.

**Operational detail**: The old PG database is not set to read-only until all
routers have been switched and the soak period is complete. This ensures that
dual-write continues to work as the fallback mechanism.

### 2.4 Monitoring During Read Cutover

| Metric | Source | Alert threshold |
|---|---|---|
| API error rate (5xx) | HTTP response codes | > 0.5% over 5 minutes |
| Read latency p50 / p99 | Application timing | p99 > 400ms |
| Supabase connection pool utilisation | Supabase dashboard / pgbouncer stats | > 80% |
| Result-set divergence (spot check) | Canary requests that query both DBs and diff results | Any mismatch |
| RLS rejection rate | Supabase logs (queries returning 0 rows unexpectedly) | Any increase from baseline |

#### Additional checks during read cutover

- **SQLite manifest diff**: After switching catalog reads to Supabase, run
  `sciona catalog sync` and compare the resulting `manifest.sqlite` byte-for-
  byte with one generated from old PG. Any difference indicates a query or
  data discrepancy.
- **Hyperparameter spot check**: Call `load_hyperparams_manifest_sqlite()` and
  compare 50 random atoms against direct Supabase queries.
- **Benchmark prior spot check**: Call `load_benchmarks_sqlite()` and compare
  10 random version benchmarks against direct Supabase queries.

### 2.5 Go/No-Go for Stage 2 Completion

All of the following must be true for at least **48 hours** with
`SCIONA_READ_SOURCE=supabase`:

- [ ] API 5xx rate below 0.1%.
- [ ] Read latency p99 below 400ms.
- [ ] No result-set divergence in canary checks.
- [ ] `sciona catalog sync` produces identical SQLite manifest from Supabase
      source as from old PG source (byte-level diff of resulting SQLite).
- [ ] `load_hyperparams_manifest_sqlite()` and `load_benchmarks_sqlite()` return
      identical results from the Supabase-generated manifest.
- [ ] RLS rejection rate is stable (no unexpected denials for authenticated users).
- [ ] No connection pool exhaustion events.

### 2.6 Rollback (Stage 2)

Set `SCIONA_READ_SOURCE=pg`. Immediate effect, no restart required if the
application watches the env var (or restart the service). Old PG is still
receiving dual-writes, so data is current.

**Per-router rollback**: If only one router is problematic, set its specific
override back to `pg` (e.g., `SCIONA_READ_SOURCE_CATALOG=pg`) without
affecting other routers that are successfully reading from Supabase.

---

## Stage 3: Full Cutover

### 3.1 Pre-Cutover Checklist

Before removing dual-write code, confirm every item. Each item must have an
owner and a verification method documented.

| # | Condition | Owner | Verification method |
|---|---|---|---|
| 1 | Stage 2 go/no-go criteria met for 48+ hours | SRE | Dashboard screenshot + metrics export |
| 2 | Final consistency check shows zero divergence (all three scripts) | SRE | Script output logs |
| 3 | `snapshot.py` generates SQLite manifests from Supabase successfully | Backend | CI pipeline green |
| 4 | `sciona catalog sync` works end-to-end against Supabase | Backend | Manual test + CI |
| 5 | `load_hyperparams_manifest_sqlite()` returns correct hyperparameters | Backend | Spot-check 10 atoms |
| 6 | `load_benchmarks_sqlite()` returns correct benchmark priors | Backend | Spot-check 10 versions |
| 7 | All CI tests pass with Supabase as sole database | Backend | CI pipeline green |
| 8 | Old PG has been set to read-only for at least 24 hours with no errors | SRE | DB logs |
| 9 | Final `pg_dump` backup of old PG completed and verified | SRE | Backup file exists + test restore |
| 10 | Rollback PR is pre-prepared (reverts the cutover PR, tested in staging) | Backend | Staging deployment |
| 11 | All team members are available during cutover window | PM | Calendar check |
| 12 | No major releases or marketing events scheduled for 48 hours post-cutover | PM | Calendar check |

### 3.2 Code Removal

The following changes are made in a single PR. The PR should be reviewed by at
least two engineers and deployed during a low-traffic window.

#### 3.2.1 Remove dual-write machinery

- Delete `sciona/api/dual_write.py` (the `DualWriteConnection` class).
- Remove the `SCIONA_DUAL_WRITE` env var check and `supabase_db_pool` creation
  from `app.py` lifespan.
- Simplify `get_db` in `deps.py` to yield a connection from the Supabase pool
  only (or replace with the Supabase PostgREST client if routers have been
  migrated to use it).
- Remove `get_dual_write_metrics()` from the healthz endpoint.

#### 3.2.2 Remove old PG connection

- Remove `SCIONA_POSTGRES_URI` env var handling from `app.py` lifespan.
- Remove the primary `asyncpg.create_pool(postgres_uri, ...)` block.
- Remove `get_read_db` and the `SCIONA_READ_SOURCE*` env vars.
- Remove the canary read comparison scripts.

#### 3.2.3 Remove legacy auth

- Delete `_get_jwt_public_key()` from `deps.py`.
- Remove `SCIONA_JWT_PUBLIC_KEY` and `SCIONA_JWT_PRIVATE_KEY` from all
  environment configurations (`.env`, `.env.example`, deployment manifests,
  CI secrets).
- Remove `SCIONA_JWT_PUBLIC_KEY_PATH` and `jwt_public_key_path` /
  `jwt_private_key_path` / `jwt_kms_key_id` from `AgeomConfig` in `config.py`.
- Remove `github_oauth_client_id` and `github_oauth_client_secret` from
  `AgeomConfig` (Supabase Auth manages OAuth now).
- Remove `PyJWT` from `pyproject.toml` / `requirements.txt`.

#### 3.2.4 Remove `/catalog/manifest` endpoint

- Delete the `download_manifest` endpoint from `catalog.py`.
- Remove `SCIONA_MANIFEST_PATH` env var handling.

#### 3.2.5 Remove `asyncpg` dependency

- Remove `asyncpg` from `pyproject.toml` / `requirements.txt`.
- If any non-API code still imports asyncpg (check with
  `grep -r "import asyncpg"`), migrate those call sites first.

#### 3.2.6 Rewrite `snapshot.py`

- `generate_manifest_sqlite()` calls Supabase (via PostgREST client or direct
  Postgres connection using the Supabase connection string) to fetch atoms,
  hyperparams, and benchmarks.
- Writes the same SQLite schema that `load_hyperparams_manifest_sqlite()` and
  `load_benchmarks_sqlite()` expect.

### 3.3 Verification After Full Cutover

Run the following verification steps in CI and manually in staging:

1. **API smoke tests**: All CRUD endpoints return expected responses.
2. **`sciona catalog sync`**: Produces a valid `manifest.sqlite` in
   `~/.sciona/`.
3. **`load_hyperparams_manifest_sqlite()`**: Returns the full hyperparameter
   manifest. Spot-check 10 atoms against Supabase rows.
4. **`load_benchmarks_sqlite()`**: Returns benchmark priors. Spot-check against
   Supabase rows.
5. **Auth flow**: `sciona login` completes successfully via Supabase Auth GitHub
   OAuth. Token is accepted by all authenticated endpoints.
6. **RLS verification**: Unauthenticated requests cannot access protected
   resources. Users with `effective_tier = 'general'` cannot see
   `visibility_tier = 'early_access'` atoms.
7. **Entitlement verification**: Create a test user with a contribution grant.
   Verify they gain `early_access` tier. Verify the grant expires correctly.
8. **Materialized view refresh**: Verify `REFRESH MATERIALIZED VIEW CONCURRENTLY
   atom_audit_latest` and `catalog_atoms_index` complete without error.

### 3.4 Environment Variable Cleanup

Remove from all deployment targets (production, staging, CI):

| Variable | Reason |
|---|---|
| `SCIONA_POSTGRES_URI` | Old PG connection string |
| `SCIONA_JWT_PUBLIC_KEY` | Legacy RS256 JWT validation |
| `SCIONA_JWT_PRIVATE_KEY` | Legacy RS256 JWT signing |
| `SCIONA_JWT_PUBLIC_KEY_PATH` | File-based key loading |
| `SCIONA_DUAL_WRITE` | Dual-write toggle |
| `SCIONA_READ_SOURCE` | Read source toggle |
| `SCIONA_READ_SOURCE_*` | Per-router read source overrides |
| `SCIONA_SUPABASE_POSTGRES_URI` | Only needed if consolidating to PostgREST client; keep if still using direct connection |
| `SCIONA_MANIFEST_PATH` | Manifest file path (endpoint removed) |

Retain:

| Variable | Reason |
|---|---|
| `SCIONA_SUPABASE_URL` | Supabase project URL |
| `SCIONA_SUPABASE_ANON_KEY` | Client-side Supabase key |
| `SCIONA_SUPABASE_SERVICE_ROLE_KEY` | Server-side Supabase key |

### 3.5 Rollback (Stage 3)

Full cutover rollback is more involved because code has been removed.

#### Immediate rollback (within 14-day bake period)

1. **Revert the PR** that removed dual-write and old PG code. The rollback PR
   should be pre-prepared and tested in staging before the cutover.
2. **Restore old PG from backup.** Before starting Stage 3, take a final
   `pg_dump` of old PG. Keep the backup for at least 30 days.
3. **Re-deploy** with `SCIONA_DUAL_WRITE=0` and `SCIONA_READ_SOURCE=pg` to
   restore the pre-cutover state.
4. **Re-sync Supabase** from old PG once the issue is resolved.

#### Data reconciliation after rollback

If Supabase received writes after the cutover that old PG did not (because
dual-write was removed), those writes must be forward-migrated to old PG:

1. Identify the cutover timestamp from deployment logs.
2. Query Supabase for all rows with `created_at > cutover_timestamp` or
   `updated_at > cutover_timestamp`.
3. Insert/update those rows into old PG.
4. Re-enable dual-write after reconciliation.

#### Risk mitigation

To reduce rollback risk, keep old PG running (read-only, no writes) for 14 days
after full cutover. Only decommission after the 14-day bake period with zero
incidents.

---

## Old PG Decommission

### Prerequisites

All of the following must be true before decommissioning old PG:

- [ ] Full cutover completed at least 14 days ago.
- [ ] Zero incidents related to Supabase during the bake period.
- [ ] No application code references `SCIONA_POSTGRES_URI` or `asyncpg`.
- [ ] No team members are querying old PG for debugging or analytics.
- [ ] Final `pg_dump` backup archived to durable storage (S3/GCS) with 90-day
      retention.

### Decommission steps

| Step | Action | Reversible? |
|---|---|---|
| 1 | Take a final `pg_dump --format=custom` backup | N/A |
| 2 | Upload backup to S3/GCS with lifecycle policy (90-day retention) | Yes (delete the object) |
| 3 | Revoke all application credentials for old PG | Yes (re-grant) |
| 4 | Set old PG to `connection_limit = 0` (reject all connections) | Yes (set back) |
| 5 | Wait 7 days. Monitor for any connection attempt alerts. | -- |
| 6 | Drop the database: `DROP DATABASE sciona;` | No (restore from backup) |
| 7 | Terminate the old PG instance (RDS/Cloud SQL/bare metal) | No (reprovision) |
| 8 | Remove DNS records pointing to old PG | Yes (re-add) |
| 9 | Remove old PG from monitoring/alerting | Yes (re-add) |
| 10 | Archive the decommission decision in the migration log | -- |

### Cost savings

After decommission, the following costs are eliminated:

- Old PG compute (RDS/Cloud SQL instance hours or bare-metal hosting).
- Old PG storage (EBS/persistent disk).
- Old PG backup storage (automated snapshots).
- Network transfer between the application and old PG.

---

## Timeline

| Day | Action | Stage |
|---|---|---|
| -7 | Pre-flight: Deploy monitoring dashboards, set up PagerDuty alerts, run consistency scripts in dry-run mode | Prep |
| -3 | Pre-flight: Audit all mutating code paths, verify no direct pool usage bypasses `get_db` | Prep |
| -1 | Pre-flight: Take baseline `pg_dump` of old PG, verify Supabase data matches | Prep |
| 0 | Deploy dual-write (`SCIONA_DUAL_WRITE=1`). Begin nightly consistency checks. | Stage 1 |
| 1--7 | Monitor dual-write. Fix any Supabase mirror failures. Run reconciliation if needed. | Stage 1 |
| 7 | **Gate**: Stage 1 go/no-go review. If passed, proceed to read cutover. | Gate |
| 8 | Switch `SCIONA_READ_SOURCE_CATALOG=supabase`. Deploy canary reads. | Stage 2 |
| 9 | Switch `SCIONA_READ_SOURCE_DASHBOARD=supabase`. | Stage 2 |
| 10 | Switch `SCIONA_READ_SOURCE_BOUNTY=supabase`. | Stage 2 |
| 11 | Switch `SCIONA_READ_SOURCE_VERIFICATION=supabase`. | Stage 2 |
| 12 | Switch `SCIONA_READ_SOURCE=supabase` (catch-all for any remaining). | Stage 2 |
| 12--14 | Monitor read cutover for 48 hours. Run SQLite manifest diff. | Stage 2 |
| 14 | **Gate**: Stage 2 go/no-go review. Set old PG to read-only. | Gate |
| 15 | Final `pg_dump` backup of old PG. Prepare rollback PR. | Stage 3 Prep |
| 16 | Merge full-cutover PR. Deploy during low-traffic window. | Stage 3 |
| 16--17 | Intensive monitoring: 5xx rate, read latency, RLS denials, auth flow. | Stage 3 |
| 17--30 | 14-day bake period. Old PG stays online, read-only, not connected. | Bake |
| 30 | **Gate**: Decommission go/no-go. | Gate |
| 30 | Begin old PG decommission sequence. | Decom |
| 37 | Drop old PG database. Terminate instance. | Decom |

### Timeline contingencies

- **If Stage 1 gate fails**: Extend by 7 days. Investigate and fix the root
  cause. Reset the 7-day counter.
- **If Stage 2 gate fails**: Roll back the problematic router(s) to `pg`.
  Investigate. Re-attempt after fix. The 48-hour counter resets.
- **If a P1 incident occurs during bake period**: Extend the bake period by
  14 days from incident resolution. If the incident required rollback to old
  PG, restart from Stage 1.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Supabase write latency spikes during dual-write | Medium | Low (old PG is authoritative) | Supabase failures are logged, not raised. Nightly reconciliation catches drift. |
| Schema mismatch between old PG and Supabase causes write failures | Low | Medium | Both schemas are derived from the same DDL. Run consistency checks on schema structure before enabling dual-write. |
| Feature flag misconfiguration reads from wrong DB | Low | High | Canary checks compare results from both DBs. Alert on any divergence. Per-router flags limit blast radius. |
| `snapshot.py` rewrite produces different SQLite schema | Medium | High | Byte-level diff of old vs. new manifest.sqlite in CI gate before read cutover. |
| Supabase PgBouncer drops connections under load | Low | Medium | `statement_cache_size=0` already set. Monitor connection pool utilisation. Test with production-level load before cutover. |
| RLS policies reject queries that worked on old PG (no RLS) | Medium | High | Run the full API test suite against Supabase with RLS enabled before enabling read cutover. Use service-role key for admin operations. Audit RLS denials in Supabase logs during Stage 2. |
| Rolling back after full cutover loses writes | Low | Critical | Keep old PG running for 14 days. Take `pg_dump` before cutover. Pre-prepare rollback PR. Forward-migrate any Supabase-only writes during reconciliation. |
| Materialized view `catalog_atoms_index` becomes stale | Medium | Medium | Schedule `REFRESH MATERIALIZED VIEW CONCURRENTLY` after every audit run and on a 15-minute cron. Monitor view staleness. |
| `effective_tier` trigger fires incorrectly after migration | Low | High | Verify `users.effective_tier` matches `user_effective_entitlement()` for all users after data migration. Add a nightly check during dual-write. |
| Dual-write causes double-counting in analytics | Medium | Low | Analytics queries should use only one source (old PG during Stage 1, Supabase during Stage 2+). Document this for the data team. |
| Old PG backup is corrupt or incomplete | Low | Critical | Verify backup with `pg_restore --list` immediately after taking it. Test a full restore to a scratch database before starting Stage 3. |

---

## Appendix A: Environment Variable Reference

### During dual-write (Stages 1--2)

```bash
# Existing (old PG)
SCIONA_POSTGRES_URI=postgresql://user:pass@old-pg-host:5432/sciona

# New (Supabase)
SCIONA_SUPABASE_URL=https://yourproject.supabase.co
SCIONA_SUPABASE_ANON_KEY=eyJ...
SCIONA_SUPABASE_SERVICE_ROLE_KEY=eyJ...
SCIONA_SUPABASE_POSTGRES_URI=postgresql://postgres:pass@db.yourproject.supabase.co:5432/postgres

# Dual-write control
SCIONA_DUAL_WRITE=1

# Read source control (Stage 2)
SCIONA_READ_SOURCE=pg                        # or 'supabase'
SCIONA_READ_SOURCE_CATALOG=supabase          # per-router override
SCIONA_READ_SOURCE_DASHBOARD=supabase
SCIONA_READ_SOURCE_BOUNTY=pg
SCIONA_READ_SOURCE_VERIFICATION=pg
```

### After full cutover (Stage 3)

```bash
# Only Supabase
SCIONA_SUPABASE_URL=https://yourproject.supabase.co
SCIONA_SUPABASE_ANON_KEY=eyJ...
SCIONA_SUPABASE_SERVICE_ROLE_KEY=eyJ...

# Keep if using direct Postgres connection (e.g., for snapshot.py)
SCIONA_SUPABASE_POSTGRES_URI=postgresql://postgres:pass@db.yourproject.supabase.co:5432/postgres
```

## Appendix B: Consistency Check Automation

### GitHub Actions workflow

```yaml
# .github/workflows/consistency-check.yml
name: Nightly Consistency Check
on:
  schedule:
    - cron: '0 3 * * *'  # 3 AM UTC daily
  workflow_dispatch: {}

jobs:
  consistency:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install asyncpg
      - name: Row count check
        env:
          SCIONA_POSTGRES_URI: ${{ secrets.SCIONA_POSTGRES_URI }}
          SCIONA_SUPABASE_POSTGRES_URI: ${{ secrets.SCIONA_SUPABASE_POSTGRES_URI }}
        run: python scripts/consistency_check_counts.py
      - name: PK checksum check
        env:
          SCIONA_POSTGRES_URI: ${{ secrets.SCIONA_POSTGRES_URI }}
          SCIONA_SUPABASE_POSTGRES_URI: ${{ secrets.SCIONA_SUPABASE_POSTGRES_URI }}
        run: python scripts/consistency_check_checksums.py
      - name: Content checksum check
        env:
          SCIONA_POSTGRES_URI: ${{ secrets.SCIONA_POSTGRES_URI }}
          SCIONA_SUPABASE_POSTGRES_URI: ${{ secrets.SCIONA_SUPABASE_POSTGRES_URI }}
        run: python scripts/consistency_check_content.py
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: consistency-reports
          path: /tmp/consistency_*.json
```

## Appendix C: Decision Log

| Date | Decision | Rationale |
|---|---|---|
| TBD | Dual-write via decorator on `get_db` vs. CDC/logical replication | Decorator approach keeps all logic in application code, avoids Postgres logical replication setup complexity. Acceptable because write volume is low (~100 writes/day). If write volume were 10K+/day, CDC would be preferred. |
| TBD | 7-day Stage 1 soak vs. 14-day | 7 days is sufficient for a low-write-volume system. The nightly checks provide daily coverage. Extend to 14 if the first week shows any reconciliation events. |
| TBD | Per-router feature flags vs. percentage-based traffic splitting | Per-router flags are simpler to implement and reason about. Percentage-based splitting requires sticky sessions or request-level routing, which adds complexity for minimal benefit at current traffic levels. |
| TBD | 14-day bake period vs. 30-day | 14 days covers two full weekly cycles. The old PG backup provides a safety net beyond the bake period. 30 days would only be warranted if there are monthly batch jobs that need to be verified. |
