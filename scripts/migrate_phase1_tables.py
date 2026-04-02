"""Phase 1 carried-forward table migration: source PostgreSQL -> Supabase."""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from phase1_common import DatabaseSettings, build_batch_params, chunked, env_float, env_int

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableSpec:
    table: str
    source_select: str
    target_insert: str
    param_columns: tuple[str, ...]
    batch_size: int = 1000
    source_relation: str | None = None

    @property
    def source_table_name(self) -> str:
        return self.source_relation or self.table


@dataclass(frozen=True)
class TableMigrationConfig:
    max_retries: int
    retry_delay_s: float
    dry_run: bool


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        table="atoms",
        batch_size=500,
        source_select="""
            SELECT atom_id, fqdn, owner_id, domain_tags, description,
                   status, superseded_by, created_at, updated_at
            FROM atoms ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.atoms (
                atom_id, fqdn, namespace_root, namespace_path, owner_id,
                domain_tags, description, status, superseded_by,
                visibility_tier, source_kind, stateful_kind, is_stochastic,
                is_ffi, is_publishable, source_package, source_module_path,
                source_symbol, created_at, updated_at
            ) VALUES (
                $1, $2, 'sciona.atoms', '', $3,
                $4, $5, $6, $7,
                'general', 'hand_written', 'none', FALSE,
                FALSE, FALSE, '', '',
                '', $8, $9
            ) ON CONFLICT (atom_id) DO NOTHING
        """,
        param_columns=(
            "atom_id", "fqdn", "owner_id", "domain_tags", "description",
            "status", "superseded_by", "created_at", "updated_at",
        ),
    ),
    TableSpec(
        table="atom_versions",
        source_select="""
            SELECT version_id, atom_id, content_hash, semver, is_latest,
                   derives_from, s3_key, fingerprint, created_at
            FROM atom_versions ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.atom_versions (
                version_id, atom_id, content_hash, semver, is_latest,
                derives_from, s3_key, fingerprint, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (version_id) DO NOTHING
        """,
        param_columns=(
            "version_id", "atom_id", "content_hash", "semver", "is_latest",
            "derives_from", "s3_key", "fingerprint", "created_at",
        ),
    ),
    TableSpec(
        table="atom_authors",
        batch_size=500,
        source_select="""
            SELECT atom_id, user_id, contribution_share
            FROM atom_authors ORDER BY atom_id, user_id
        """,
        target_insert="""
            INSERT INTO public.atom_authors (atom_id, user_id, contribution_share)
            VALUES ($1,$2,$3)
            ON CONFLICT (atom_id, user_id) DO NOTHING
        """,
        param_columns=("atom_id", "user_id", "contribution_share"),
    ),
    TableSpec(
        table="hyperparams",
        source_select="""
            SELECT hp_id, atom_id, name, kind, default_value, min_value,
                   max_value, step_value, log_scale, choices_json,
                   constraints_json, semantic_role, status
            FROM hyperparams ORDER BY hp_id
        """,
        target_insert="""
            INSERT INTO public.hyperparams (
                hp_id, atom_id, name, kind, default_value, min_value,
                max_value, step_value, log_scale, choices_json,
                constraints_json, semantic_role, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (hp_id) DO NOTHING
        """,
        param_columns=(
            "hp_id", "atom_id", "name", "kind", "default_value", "min_value",
            "max_value", "step_value", "log_scale", "choices_json",
            "constraints_json", "semantic_role", "status",
        ),
    ),
    TableSpec(
        table="atom_benchmarks",
        source_select="""
            SELECT benchmark_id, version_id, benchmark_name, metric_name,
                   metric_value, dataset_tag, measured_at
            FROM atom_benchmarks ORDER BY benchmark_id
        """,
        target_insert="""
            INSERT INTO public.atom_benchmarks (
                benchmark_id, version_id, benchmark_name, metric_name,
                metric_value, dataset_tag, measured_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (benchmark_id) DO NOTHING
        """,
        param_columns=(
            "benchmark_id", "version_id", "benchmark_name", "metric_name",
            "metric_value", "dataset_tag", "measured_at",
        ),
    ),
    TableSpec(
        table="bounties",
        batch_size=500,
        source_select="""
            SELECT bounty_id, principal_id, title, escrow_amount, status,
                   deadline, tier, verification_budget, verifications_used,
                   config_yml, flare_payload, ageom_yml_s3, dataset_s3,
                   public_split_hash, blind_split_hash, cancellation_fee,
                   reposted_from, created_at, updated_at
            FROM bounties ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.bounties (
                bounty_id, principal_id, title, escrow_amount, status,
                deadline, tier, verification_budget, verifications_used,
                config_yml, flare_payload, ageom_yml_s3, dataset_s3,
                public_split_hash, blind_split_hash, cancellation_fee,
                reposted_from, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            ON CONFLICT (bounty_id) DO NOTHING
        """,
        param_columns=(
            "bounty_id", "principal_id", "title", "escrow_amount", "status",
            "deadline", "tier", "verification_budget", "verifications_used",
            "config_yml", "flare_payload", "ageom_yml_s3", "dataset_s3",
            "public_split_hash", "blind_split_hash", "cancellation_fee",
            "reposted_from", "created_at", "updated_at",
        ),
    ),
    TableSpec(
        table="submissions",
        batch_size=500,
        source_select="""
            SELECT submission_id, bounty_id, architect_id, cdg_hash,
                   atom_versions, receipt_s3, receipt_json,
                   claimed_metric_name, claimed_metric_value,
                   verified_metric_value, verification_status,
                   is_winner, submitted_at, verified_at
            FROM submissions ORDER BY submitted_at
        """,
        target_insert="""
            INSERT INTO public.submissions (
                submission_id, bounty_id, architect_id, cdg_hash,
                atom_versions, receipt_s3, receipt_json,
                claimed_metric_name, claimed_metric_value,
                verified_metric_value, verification_status,
                is_winner, submitted_at, verified_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (submission_id) DO NOTHING
        """,
        param_columns=(
            "submission_id", "bounty_id", "architect_id", "cdg_hash",
            "atom_versions", "receipt_s3", "receipt_json",
            "claimed_metric_name", "claimed_metric_value",
            "verified_metric_value", "verification_status",
            "is_winner", "submitted_at", "verified_at",
        ),
    ),
    TableSpec(
        table="payouts",
        batch_size=500,
        source_select="""
            SELECT payout_id, bounty_id, user_id, role, amount,
                   shapley_value, stripe_transfer_id, status, created_at
            FROM payouts ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.payouts (
                payout_id, bounty_id, user_id, role, amount,
                shapley_value, stripe_transfer_id, status, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (payout_id) DO NOTHING
        """,
        param_columns=(
            "payout_id", "bounty_id", "user_id", "role", "amount",
            "shapley_value", "stripe_transfer_id", "status", "created_at",
        ),
    ),
    TableSpec(
        table="verification_budgets",
        source_select="""
            SELECT bounty_id, tier, total_slots, used_slots,
                   cost_per_extra, overhead_deposit, overhead_used, created_at
            FROM verification_budgets ORDER BY bounty_id
        """,
        target_insert="""
            INSERT INTO public.verification_budgets (
                bounty_id, tier, total_slots, used_slots,
                cost_per_extra, overhead_deposit, overhead_used, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (bounty_id) DO NOTHING
        """,
        param_columns=(
            "bounty_id", "tier", "total_slots", "used_slots",
            "cost_per_extra", "overhead_deposit", "overhead_used", "created_at",
        ),
    ),
    TableSpec(
        table="verification_runs",
        source_select="""
            SELECT id, bounty_id, submission_id, split_type, status,
                   metric_values, output_hash, execution_time_s,
                   peak_memory_bytes, is_deterministic, sandbox_job_id,
                   slot_consumed, started_at, completed_at, created_at
            FROM verification_runs ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.verification_runs (
                id, bounty_id, submission_id, split_type, status,
                metric_values, output_hash, execution_time_s,
                peak_memory_bytes, is_deterministic, sandbox_job_id,
                slot_consumed, started_at, completed_at, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (id) DO NOTHING
        """,
        param_columns=(
            "id", "bounty_id", "submission_id", "split_type", "status",
            "metric_values", "output_hash", "execution_time_s",
            "peak_memory_bytes", "is_deterministic", "sandbox_job_id",
            "slot_consumed", "started_at", "completed_at", "created_at",
        ),
    ),
    TableSpec(
        table="bounty_best_scores",
        source_select="""
            SELECT bounty_id, metric_name, best_value,
                   best_submission_id, is_baseline, updated_at
            FROM bounty_best_scores ORDER BY bounty_id, metric_name
        """,
        target_insert="""
            INSERT INTO public.bounty_best_scores (
                bounty_id, metric_name, best_value,
                best_submission_id, is_baseline, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (bounty_id, metric_name) DO NOTHING
        """,
        param_columns=(
            "bounty_id", "metric_name", "best_value",
            "best_submission_id", "is_baseline", "updated_at",
        ),
    ),
    TableSpec(
        table="principal_targets",
        source_select="""
            SELECT id, bounty_id, metric_name, target_value, set_by, created_at
            FROM principal_targets ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.principal_targets (
                id, bounty_id, metric_name, target_value, set_by, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING
        """,
        param_columns=("id", "bounty_id", "metric_name", "target_value", "set_by", "created_at"),
    ),
    TableSpec(
        table="execution_receipts",
        batch_size=500,
        source_select="""
            SELECT id, submission_id, bounty_id, cdg_hash, atom_versions,
                   split_hash, output_hash, metric_name, metric_value,
                   ageom_version, ssh_signature, ssh_public_key,
                   verified, receipt_timestamp, created_at
            FROM execution_receipts ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.execution_receipts (
                id, submission_id, bounty_id, cdg_hash, atom_versions,
                split_hash, output_hash, metric_name, metric_value,
                ageom_version, ssh_signature, ssh_public_key,
                verified, receipt_timestamp, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (id) DO NOTHING
        """,
        param_columns=(
            "id", "submission_id", "bounty_id", "cdg_hash", "atom_versions",
            "split_hash", "output_hash", "metric_name", "metric_value",
            "ageom_version", "ssh_signature", "ssh_public_key",
            "verified", "receipt_timestamp", "created_at",
        ),
    ),
    TableSpec(
        table="dataset_splits",
        source_select="""
            SELECT id, bounty_id, unit_key, partition, created_at
            FROM dataset_splits ORDER BY bounty_id, unit_key
        """,
        target_insert="""
            INSERT INTO public.dataset_splits (
                id, bounty_id, unit_key, partition, created_at
            ) VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (id) DO NOTHING
        """,
        param_columns=("id", "bounty_id", "unit_key", "partition", "created_at"),
    ),
    TableSpec(
        table="settlement_payouts",
        source_select="""
            SELECT id, bounty_id, recipient_id, role, amount,
                   stripe_transfer_id, atom_fqdn, cdg_hash, created_at
            FROM settlement_payouts ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.settlement_payouts (
                id, bounty_id, recipient_id, role, amount,
                stripe_transfer_id, atom_fqdn, cdg_hash, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (id) DO NOTHING
        """,
        param_columns=(
            "id", "bounty_id", "recipient_id", "role", "amount",
            "stripe_transfer_id", "atom_fqdn", "cdg_hash", "created_at",
        ),
    ),
    TableSpec(
        table="benchmark_suites",
        source_select="""
            SELECT benchmark_id, domain_tags, description, dataset_s3_key,
                   metric_names, curation_source, proposer_id, vote_count,
                   status, created_at
            FROM benchmark_suites ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.benchmark_suites (
                benchmark_id, domain_tags, description, dataset_s3_key,
                metric_names, curation_source, proposer_id, vote_count,
                status, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (benchmark_id) DO NOTHING
        """,
        param_columns=(
            "benchmark_id", "domain_tags", "description", "dataset_s3_key",
            "metric_names", "curation_source", "proposer_id", "vote_count",
            "status", "created_at",
        ),
    ),
    TableSpec(
        table="benchmark_votes",
        source_select="""
            SELECT benchmark_id, voter_id, vote, created_at
            FROM benchmark_votes ORDER BY benchmark_id, voter_id
        """,
        target_insert="""
            INSERT INTO public.benchmark_votes (
                benchmark_id, voter_id, vote, created_at
            ) VALUES ($1,$2,$3,$4)
            ON CONFLICT (benchmark_id, voter_id) DO NOTHING
        """,
        param_columns=("benchmark_id", "voter_id", "vote", "created_at"),
    ),
    TableSpec(
        table="fuzz_results",
        source_select="""
            SELECT fuzz_id, atom_fqdn, content_hash, strategy, passed,
                   failures, inputs_tested, runtime_ms, created_at
            FROM fuzz_results ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.fuzz_results (
                fuzz_id, atom_fqdn, content_hash, strategy, passed,
                failures, inputs_tested, runtime_ms, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (fuzz_id) DO NOTHING
        """,
        param_columns=(
            "fuzz_id", "atom_fqdn", "content_hash", "strategy", "passed",
            "failures", "inputs_tested", "runtime_ms", "created_at",
        ),
    ),
    TableSpec(
        table="behavioral_equivalence_flags",
        source_select="""
            SELECT flag_id, atom_a_fqdn, atom_a_hash, atom_b_fqdn, atom_b_hash,
                   match_ratio, sample_size, reviewed, reviewer_id,
                   disposition, created_at
            FROM behavioral_equivalence_flags ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.behavioral_equivalence_flags (
                flag_id, atom_a_fqdn, atom_a_hash, atom_b_fqdn, atom_b_hash,
                match_ratio, sample_size, reviewed, reviewer_id,
                disposition, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (flag_id) DO NOTHING
        """,
        param_columns=(
            "flag_id", "atom_a_fqdn", "atom_a_hash", "atom_b_fqdn", "atom_b_hash",
            "match_ratio", "sample_size", "reviewed", "reviewer_id",
            "disposition", "created_at",
        ),
    ),
    TableSpec(
        table="discipline_repos",
        source_select="""
            SELECT repo_id, repo_url, webhook_secret, domain_tags,
                   maintainer_ids, last_synced_commit, status,
                   created_at, updated_at
            FROM discipline_repos ORDER BY created_at
        """,
        target_insert="""
            INSERT INTO public.discipline_repos (
                repo_id, repo_url, webhook_secret, domain_tags,
                maintainer_ids, last_synced_commit, status,
                created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (repo_id) DO NOTHING
        """,
        param_columns=(
            "repo_id", "repo_url", "webhook_secret", "domain_tags",
            "maintainer_ids", "last_synced_commit", "status",
            "created_at", "updated_at",
        ),
    ),
)

MIGRATION_ORDER: tuple[str, ...] = (
    "atoms",
    "atom_versions",
    "atom_authors",
    "hyperparams",
    "atom_benchmarks",
    "bounties",
    "submissions",
    "payouts",
    "verification_budgets",
    "verification_runs",
    "bounty_best_scores",
    "principal_targets",
    "execution_receipts",
    "dataset_splits",
    "settlement_payouts",
    "benchmark_suites",
    "benchmark_votes",
    "fuzz_results",
    "behavioral_equivalence_flags",
    "discipline_repos",
)


def spec_map() -> dict[str, TableSpec]:
    return {spec.table: spec for spec in TABLE_SPECS}


def resolve_table_specs(requested_tables: Sequence[str] | None) -> list[TableSpec]:
    mapping = spec_map()
    names = list(requested_tables or MIGRATION_ORDER)
    missing = [name for name in names if name not in mapping]
    if missing:
        raise ValueError(f"Unknown table(s): {', '.join(sorted(missing))}")
    return [mapping[name] for name in names]


async def source_table_exists(src: Any, relation_name: str) -> bool:
    return bool(await src.fetchval("SELECT to_regclass($1)", relation_name))


async def migrate_table(
    src: Any,
    dst: Any,
    spec: TableSpec,
    config: TableMigrationConfig,
) -> int:
    if not await source_table_exists(src, spec.source_table_name):
        log.warning("[%s] source table missing, skipping", spec.table)
        return 0

    rows = [dict(row) for row in await src.fetch(spec.source_select)]
    if not rows:
        log.info("[%s] 0 rows (empty source)", spec.table)
        return 0

    migrated = 0
    for index, batch in enumerate(chunked(rows, spec.batch_size), start=1):
        params = build_batch_params(batch, spec.param_columns)
        for attempt in range(1, config.max_retries + 1):
            try:
                if not config.dry_run:
                    await dst.executemany(spec.target_insert, params)
                migrated += len(batch)
                break
            except Exception as exc:
                if attempt >= config.max_retries:
                    raise RuntimeError(
                        f"[{spec.table}] batch {index} failed after {config.max_retries} attempts"
                    ) from exc
                log.warning(
                    "[%s] batch %d failed (%s); retrying",
                    spec.table,
                    index,
                    exc,
                )
                await asyncio.sleep(config.retry_delay_s * attempt)
        log.info(
            "[%s] processed batch %d (%d rows)%s",
            spec.table,
            index,
            len(batch),
            " [dry-run]" if config.dry_run else "",
        )
    return migrated


async def migrate_tables(
    database: DatabaseSettings,
    selected_specs: Sequence[TableSpec],
    config: TableMigrationConfig,
) -> None:
    import asyncpg

    src = await asyncpg.connect(database.source_database_url)
    dst = await asyncpg.connect(database.supabase_database_url)
    try:
        for spec in selected_specs:
            migrated = await migrate_table(src, dst, spec, config)
            log.info("[%s] migrated %d rows", spec.table, migrated)
    finally:
        await src.close()
        await dst.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tables", nargs="*", help="Optional subset of tables to migrate")
    parser.add_argument("--max-retries", type=int, default=env_int("PHASE1_MAX_RETRIES", 3))
    parser.add_argument(
        "--retry-delay-s",
        type=float,
        default=env_float("PHASE1_RETRY_DELAY_S", 2.0),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    await migrate_tables(
        DatabaseSettings.from_env(),
        resolve_table_specs(args.tables),
        TableMigrationConfig(
            max_retries=args.max_retries,
            retry_delay_s=args.retry_delay_s,
            dry_run=args.dry_run,
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
