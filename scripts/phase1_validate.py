"""Phase 1 validation: row counts, checksums, and FK spot checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from phase1_common import DatabaseSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

VALIDATION_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("users", ("user_id", "github_id", "github_login", "email", "reputation_score")),
    ("atoms", ("atom_id", "fqdn", "owner_id", "status")),
    ("atom_versions", ("version_id", "atom_id", "content_hash", "semver")),
    ("atom_authors", ("atom_id", "user_id", "contribution_share")),
    ("hyperparams", ("hp_id", "atom_id", "name", "kind")),
    ("atom_benchmarks", ("benchmark_id", "version_id", "metric_value")),
    ("bounties", ("bounty_id", "principal_id", "title", "escrow_amount", "status")),
    ("submissions", ("submission_id", "bounty_id", "architect_id", "verification_status")),
    ("payouts", ("payout_id", "bounty_id", "user_id", "amount", "status")),
    ("verification_budgets", ("bounty_id", "tier", "total_slots", "used_slots")),
    ("verification_runs", ("id", "bounty_id", "submission_id", "status")),
    ("bounty_best_scores", ("bounty_id", "metric_name", "best_value")),
    ("principal_targets", ("id", "bounty_id", "metric_name", "target_value")),
    ("execution_receipts", ("id", "submission_id", "bounty_id", "metric_value")),
    ("dataset_splits", ("id", "bounty_id", "unit_key", "partition")),
    ("settlement_payouts", ("id", "bounty_id", "recipient_id", "amount")),
    ("benchmark_suites", ("benchmark_id", "curation_source", "status")),
    ("benchmark_votes", ("benchmark_id", "voter_id", "vote")),
    ("fuzz_results", ("fuzz_id", "atom_fqdn", "strategy", "passed")),
    (
        "behavioral_equivalence_flags",
        ("flag_id", "atom_a_fqdn", "atom_b_fqdn", "match_ratio"),
    ),
    ("discipline_repos", ("repo_id", "repo_url", "status")),
)


async def compute_checksum(conn: Any, table: str, columns: tuple[str, ...]) -> str:
    expr = " || '|' || ".join(f"COALESCE({column}::text, '')" for column in columns)
    query = (
        f"SELECT md5(string_agg({expr}, E'\\n' ORDER BY {columns[0]}::text)) "
        f"FROM {table}"
    )
    return await conn.fetchval(query) or "<empty>"


async def validate_table(
    src: Any,
    dst: Any,
    table: str,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    src_count = await src.fetchval(f"SELECT count(*) FROM {table}")
    dst_count = await dst.fetchval(f"SELECT count(*) FROM public.{table}")
    count_match = src_count == dst_count

    if count_match:
        src_checksum = await compute_checksum(src, table, columns)
        dst_checksum = await compute_checksum(dst, f"public.{table}", columns)
        checksum_match = src_checksum == dst_checksum
    else:
        src_checksum = "<skipped>"
        dst_checksum = "<skipped>"
        checksum_match = False

    passed = count_match and checksum_match
    log.info(
        "%s %-30s src=%-6d dst=%-6d count=%s checksum=%s",
        "PASS" if passed else "FAIL",
        table,
        src_count,
        dst_count,
        "ok" if count_match else "mismatch",
        "ok" if checksum_match else "mismatch",
    )
    return {
        "table": table,
        "src_count": src_count,
        "dst_count": dst_count,
        "count_match": count_match,
        "checksum_match": checksum_match,
        "src_checksum": src_checksum,
        "dst_checksum": dst_checksum,
        "passed": passed,
    }


async def validate_fk_integrity(dst: Any) -> list[dict[str, Any]]:
    checks = (
        (
            "atoms.owner_id -> users",
            """
            SELECT count(*) FROM public.atoms a
            LEFT JOIN public.users u ON u.user_id = a.owner_id
            WHERE u.user_id IS NULL
            """,
        ),
        (
            "atom_versions.atom_id -> atoms",
            """
            SELECT count(*) FROM public.atom_versions av
            LEFT JOIN public.atoms a ON a.atom_id = av.atom_id
            WHERE a.atom_id IS NULL
            """,
        ),
        (
            "atom_authors.user_id -> users",
            """
            SELECT count(*) FROM public.atom_authors aa
            LEFT JOIN public.users u ON u.user_id = aa.user_id
            WHERE u.user_id IS NULL
            """,
        ),
        (
            "bounties.principal_id -> users",
            """
            SELECT count(*) FROM public.bounties b
            LEFT JOIN public.users u ON u.user_id = b.principal_id
            WHERE u.user_id IS NULL
            """,
        ),
        (
            "submissions.bounty_id -> bounties",
            """
            SELECT count(*) FROM public.submissions s
            LEFT JOIN public.bounties b ON b.bounty_id = s.bounty_id
            WHERE b.bounty_id IS NULL
            """,
        ),
        (
            "auth.users <-> public.users 1:1",
            """
            SELECT count(*) FROM auth.users au
            LEFT JOIN public.users pu ON pu.user_id = au.id
            WHERE pu.user_id IS NULL
            """,
        ),
    )

    results: list[dict[str, Any]] = []
    for name, query in checks:
        orphans = await dst.fetchval(query)
        passed = orphans == 0
        log.info("%s FK %-35s orphans=%d", "PASS" if passed else "FAIL", name, orphans)
        results.append({"check": name, "orphans": orphans, "passed": passed})
    return results


async def run_validation(database: DatabaseSettings) -> int:
    import asyncpg

    src = await asyncpg.connect(database.source_database_url)
    dst = await asyncpg.connect(database.supabase_database_url)
    try:
        table_results = [
            await validate_table(src, dst, table, columns)
            for table, columns in VALIDATION_TABLES
        ]
        fk_results = await validate_fk_integrity(dst)
    finally:
        await src.close()
        await dst.close()

    failures = [result for result in table_results if not result["passed"]]
    fk_failures = [result for result in fk_results if not result["passed"]]
    if failures or fk_failures:
        log.error("Phase 1 validation failed")
        for result in failures:
            log.error("TABLE FAILURE: %s", json.dumps(result, default=str))
        for result in fk_failures:
            log.error("FK FAILURE: %s", json.dumps(result, default=str))
        return 1

    log.info("Phase 1 validation passed")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


async def main() -> None:
    parse_args()
    raise SystemExit(await run_validation(DatabaseSettings.from_env()))


if __name__ == "__main__":
    asyncio.run(main())
