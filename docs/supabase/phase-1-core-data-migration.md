# Phase 1: Core Data Migration

**Status**: Not started
**Depends on**: Phase 0 (Supabase project provisioned, GitHub OAuth enabled, schema DDL applied)
**Estimated effort**: 3-4 days

---

## 1. Overview

Phase 1 migrates all existing relational data from the current PostgreSQL instance
into the Supabase-hosted project. This covers the "carried forward" tables whose
structure is identical or nearly identical between source and target, plus the
user table which requires coordination with Supabase Auth.

### Scope

| Source table | Target table | Notes |
|---|---|---|
| `users` | `auth.users` + `public.users` | PK changes from self-generated UUID to Supabase Auth UUID |
| _(new)_ | `public.organizations` | Seed from config; no source table exists |
| _(new)_ | `public.organization_memberships` | Seed from config; auto-assigned by email domain |
| `atoms` | `public.atoms` | New columns added with defaults |
| `atom_versions` | `public.atom_versions` | Structurally identical |
| `atom_authors` | `public.atom_authors` | Structurally identical |
| `hyperparams` | `public.hyperparams` | Structurally identical |
| `atom_benchmarks` | `public.atom_benchmarks` | Structurally identical |
| `bounties` | `public.bounties` | Structurally identical |
| `submissions` | `public.submissions` | Structurally identical |
| `payouts` | `public.payouts` | Structurally identical |
| `verification_budgets` | `public.verification_budgets` | Structurally identical |
| `verification_runs` | `public.verification_runs` | Structurally identical |
| `bounty_best_scores` | `public.bounty_best_scores` | Structurally identical |
| `principal_targets` | `public.principal_targets` | Structurally identical |
| `execution_receipts` | `public.execution_receipts` | Structurally identical |
| `dataset_splits` | `public.dataset_splits` | Structurally identical |
| `settlement_payouts` | `public.settlement_payouts` | Structurally identical |
| `benchmark_suites` | `public.benchmark_suites` | Structurally identical |
| `benchmark_votes` | `public.benchmark_votes` | Structurally identical |
| `fuzz_results` | `public.fuzz_results` | Structurally identical |
| `behavioral_equivalence_flags` | `public.behavioral_equivalence_flags` | Structurally identical |
| `discipline_repos` | `public.discipline_repos` | Structurally identical |

### Not in scope (later phases)

- `atom_io_specs`, `atom_parameters`, `atom_descriptions`, `atom_references` (Phase 2 backfill from files)
- `atom_audit_evidence`, `atom_audit_rollups` (Phase 2b backfill from `audit_manifest.json`)
- `atom_uncertainty_estimates`, `atom_verification_matches` (Phase 2d backfill from JSON files)
- `roles`, `user_role_assignments`, `user_memberships`, `user_entitlement_grants` (Phase 2e)
- `atom_source_repositories` and namespace field backfill (Phase 2e)
- RLS policies (Phase 3)
- Dual-write / read cutover (Phase 3+)

---

## 2. Prerequisites

- Phase 0 complete: Supabase project provisioned, CLI linked, full schema DDL applied.
- Source PostgreSQL connection string available as `SOURCE_DATABASE_URL`.
- Supabase direct Postgres connection string available as `SUPABASE_DATABASE_URL`
  (from Supabase dashboard > Settings > Database > Connection string, session-mode
  pooler for transactional writes).
- Supabase service role key available as `SUPABASE_SERVICE_KEY` (for Admin API
  calls to create `auth.users` entries).
- Supabase project URL available as `SUPABASE_URL`.
- Python 3.11+ with `asyncpg`, `httpx` installed.

---

## 3. User Migration

User migration is the most complex step because the source `users.user_id` is a
self-generated UUID, but the target `public.users.user_id` must be an FK to
`auth.users.id` -- a UUID assigned by Supabase Auth. Every downstream FK
(`atoms.owner_id`, `atom_authors.user_id`, `bounties.principal_id`, etc.) must
be remapped.

### Strategy

1. Read all rows from source `users`.
2. For each user, call the Supabase Admin API to create an `auth.users` entry
   linked to the GitHub provider identity. The Admin API allows specifying a
   custom UUID, so we **preserve the original `user_id`** to avoid remapping all
   downstream FKs.
3. Disable the `handle_new_user` trigger before migration to prevent the trigger
   from auto-creating profile rows with incomplete metadata. Insert `public.users`
   rows directly with exact source data plus the new `effective_tier` column
   defaulted to `'general'`.
4. Re-enable the trigger after migration so future signups work normally.

### Data transformation: `users`

| Source column | Target column | Transformation |
|---|---|---|
| `user_id` | `user_id` | Preserved (passed as custom UUID to Admin API) |
| `github_id` | `github_id` | Direct copy |
| `github_login` | `github_login` | Direct copy |
| `display_name` | `display_name` | Direct copy |
| `avatar_url` | `avatar_url` | Direct copy |
| `email` | `email` | Direct copy |
| `identity_tier` | `identity_tier` | Direct copy |
| `stripe_account_id` | `stripe_account_id` | Direct copy |
| `reputation_score` | `reputation_score` | Direct copy |
| `is_blacklisted` | `is_blacklisted` | Direct copy |
| _(none)_ | `effective_tier` | Default `'general'`; recomputed after entitlement grants are seeded in Phase 2e |
| `created_at` | `created_at` | Direct copy |
| `updated_at` | `updated_at` | Direct copy |

For `auth.users`, each row is created via the Admin API:

```python
{
    "id": str(source_user["user_id"]),  # preserve UUID
    "email": source_user["email"] or f'{source_user["github_login"]}@github-noreply.example',
    "email_confirm": True,
    "user_metadata": {
        "provider_id": str(source_user["github_id"]),
        "user_name": source_user["github_login"],
        "full_name": source_user["display_name"],
        "avatar_url": source_user["avatar_url"],
    },
}
```

### Script: `scripts/migrate_phase1_users.py`

```python
"""Phase 1 user migration: source PG -> Supabase Auth + public.users."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 500
MAX_RETRIES = 3
RETRY_DELAY_S = 2.0

SOURCE_DATABASE_URL = os.environ["SOURCE_DATABASE_URL"]
SUPABASE_DATABASE_URL = os.environ["SUPABASE_DATABASE_URL"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


async def fetch_source_users(src: asyncpg.Connection) -> list[dict]:
    rows = await src.fetch("SELECT * FROM users ORDER BY created_at")
    return [dict(r) for r in rows]


async def create_auth_user(client: httpx.AsyncClient, user: dict) -> None:
    """Create an auth.users entry via the Supabase Admin API."""
    email = user["email"] or f'{user["github_login"]}@github-noreply.example'
    payload = {
        "id": str(user["user_id"]),
        "email": email,
        "email_confirm": True,
        "user_metadata": {
            "provider_id": str(user["github_id"]),
            "user_name": user["github_login"],
            "full_name": user["display_name"],
            "avatar_url": user["avatar_url"],
        },
    }
    for attempt in range(1, MAX_RETRIES + 1):
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            json=payload,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code in (200, 201):
            return
        if resp.status_code == 422 and "already been registered" in resp.text:
            log.info("auth.users row already exists for %s (idempotent skip)",
                     user["github_login"])
            return
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("retry-after", RETRY_DELAY_S * attempt))
            log.warning("Rate limited on %s, waiting %.1fs", user["github_login"], retry_after)
            await asyncio.sleep(retry_after)
            continue
        if attempt < MAX_RETRIES:
            log.warning(
                "Auth create failed for %s (attempt %d/%d): %d %s",
                user["github_login"], attempt, MAX_RETRIES,
                resp.status_code, resp.text[:200],
            )
            await asyncio.sleep(RETRY_DELAY_S * attempt)
        else:
            raise RuntimeError(
                f"Failed to create auth user {user['github_login']} after "
                f"{MAX_RETRIES} attempts: {resp.status_code} {resp.text[:500]}"
            )


async def insert_public_users_batch(
    dst: asyncpg.Connection, batch: list[dict]
) -> None:
    """Insert a batch of public.users rows directly (trigger disabled)."""
    await dst.executemany(
        """
        INSERT INTO public.users (
            user_id, github_id, github_login, display_name, avatar_url,
            email, identity_tier, stripe_account_id, reputation_score,
            is_blacklisted, effective_tier, created_at, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (user_id) DO NOTHING
        """,
        [
            (
                u["user_id"], u["github_id"], u["github_login"],
                u["display_name"], u["avatar_url"], u["email"],
                u["identity_tier"], u.get("stripe_account_id"),
                u["reputation_score"], u["is_blacklisted"],
                "general",  # effective_tier -- recomputed after grants seeded
                u["created_at"], u["updated_at"],
            )
            for u in batch
        ],
    )


async def migrate_users() -> None:
    src = await asyncpg.connect(SOURCE_DATABASE_URL)
    dst = await asyncpg.connect(SUPABASE_DATABASE_URL)

    try:
        users = await fetch_source_users(src)
        log.info("Fetched %d users from source", len(users))

        # Step 1: Disable the auto-profile trigger so we control the insert.
        await dst.execute(
            "ALTER TABLE auth.users DISABLE TRIGGER on_auth_user_created"
        )
        log.info("Disabled on_auth_user_created trigger")

        # Step 2: Create auth.users entries via Admin API.
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(0, len(users), BATCH_SIZE):
                batch = users[i : i + BATCH_SIZE]
                for user in batch:
                    await create_auth_user(client, user)
                log.info(
                    "Created auth.users %d-%d / %d",
                    i + 1, min(i + BATCH_SIZE, len(users)), len(users),
                )

        # Step 3: Bulk-insert public.users rows.
        for i in range(0, len(users), BATCH_SIZE):
            batch = users[i : i + BATCH_SIZE]
            await insert_public_users_batch(dst, batch)
            log.info(
                "Inserted public.users %d-%d / %d",
                i + 1, min(i + BATCH_SIZE, len(users)), len(users),
            )

        # Step 4: Re-enable the trigger.
        await dst.execute(
            "ALTER TABLE auth.users ENABLE TRIGGER on_auth_user_created"
        )
        log.info("Re-enabled on_auth_user_created trigger")

        log.info("User migration complete: %d users", len(users))
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    asyncio.run(migrate_users())
```

---

## 4. Organization and Membership Seeding

The source database has no `organizations` or `organization_memberships` tables.
These are new Supabase-side constructs. Phase 1 seeds them from a configuration
file. After users are migrated, memberships are auto-assigned by matching user
email domains.

### Script: `scripts/migrate_phase1_organizations.py`

```python
"""Phase 1 organization seeding: create orgs and auto-assign memberships."""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPABASE_DATABASE_URL = os.environ["SUPABASE_DATABASE_URL"]

# Seed data -- adjust to your actual organizations.
SEED_ORGS = [
    {
        "name": "Sciona Foundation",
        "entitlement_tier": "internal",
        "email_domains": ["sciona.org"],
    },
    # Add more organizations as needed before running.
]


async def seed_organizations() -> None:
    dst = await asyncpg.connect(SUPABASE_DATABASE_URL)
    try:
        for org in SEED_ORGS:
            org_id = await dst.fetchval(
                """
                INSERT INTO public.organizations (name, entitlement_tier)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                RETURNING organization_id
                """,
                org["name"], org["entitlement_tier"],
            )
            if org_id is None:
                log.info("Organization '%s' already exists, skipping", org["name"])
                continue
            for domain in org.get("email_domains", []):
                await dst.execute(
                    """
                    INSERT INTO public.organization_email_domains
                        (organization_id, email_domain)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    org_id, domain,
                )
            log.info("Seeded organization '%s' (%s) with domains %s",
                     org["name"], org_id, org.get("email_domains", []))

        # Auto-assign memberships based on email domain.
        result = await dst.execute(
            """
            INSERT INTO public.organization_memberships
                (organization_id, user_id, membership_source)
            SELECT oed.organization_id, u.user_id, 'email_domain'
            FROM public.users u
            JOIN public.organization_email_domains oed
              ON u.email LIKE '%%@' || oed.email_domain
            ON CONFLICT DO NOTHING
            """
        )
        log.info("Auto-assigned organization memberships by email domain: %s", result)
    finally:
        await dst.close()


if __name__ == "__main__":
    asyncio.run(seed_organizations())
```

---

## 5. Core Table Migration

### 5.1 Data transformation: `atoms`

The source `atoms` table has fewer columns than the target. New columns are
populated with defaults:

| Source column | Target column | Transformation |
|---|---|---|
| `atom_id` | `atom_id` | Direct copy |
| `fqdn` | `fqdn` | Direct copy |
| _(none)_ | `namespace_root` | Default `'sciona.atoms'` |
| _(none)_ | `namespace_path` | Default `''` (backfilled in Phase 2e) |
| `owner_id` | `owner_id` | Direct copy (UUIDs preserved from user migration) |
| `domain_tags` | `domain_tags` | Direct copy |
| `description` | `description` | Direct copy |
| `status` | `status` | Direct copy |
| `superseded_by` | `superseded_by` | Direct copy (may be NULL) |
| _(none)_ | `visibility_tier` | Default `'general'` |
| _(none)_ | `source_kind` | Default `'hand_written'` (backfilled in Phase 2e) |
| _(none)_ | `stateful_kind` | Default `'none'` (backfilled in Phase 2e) |
| _(none)_ | `is_stochastic` | Default `FALSE` (backfilled in Phase 2e) |
| _(none)_ | `is_ffi` | Default `FALSE` (backfilled in Phase 2e) |
| _(none)_ | `is_publishable` | Default `FALSE` (recomputed after all Phase 2 backfills) |
| _(none)_ | `source_repo_id` | Default `NULL` (backfilled in Phase 2e) |
| _(none)_ | `source_package` | Default `''` (backfilled in Phase 2e) |
| _(none)_ | `source_module_path` | Default `''` (backfilled in Phase 2e) |
| _(none)_ | `source_symbol` | Default `''` (backfilled in Phase 2e) |
| `created_at` | `created_at` | Direct copy |
| `updated_at` | `updated_at` | Direct copy |

### 5.2 Tables with identical structure

These tables require no transformation beyond direct row copy:

- `atom_versions`
- `atom_authors`
- `hyperparams`
- `atom_benchmarks`
- `bounties`
- `submissions`
- `payouts`
- All Phase C verification tables
- All Phase D ecosystem tables

### 5.3 Generic table migration script

All tables are migrated by a single parameterized script that reads from source
via asyncpg and writes to target via asyncpg.

### Script: `scripts/migrate_phase1_tables.py`

```python
"""Phase 1 generic table migration: source PG -> Supabase PG.

Migrates tables with identical or near-identical schemas in batches of 500-1000
rows with retry logic and ON CONFLICT DO NOTHING for idempotency.

Usage:
    python scripts/migrate_phase1_tables.py              # all tables
    python scripts/migrate_phase1_tables.py atoms bounties  # specific tables only
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 1000
MAX_RETRIES = 3
RETRY_DELAY_S = 2.0

SOURCE_DATABASE_URL = os.environ["SOURCE_DATABASE_URL"]
SUPABASE_DATABASE_URL = os.environ["SUPABASE_DATABASE_URL"]


# ---------------------------------------------------------------------------
# Table specifications
# ---------------------------------------------------------------------------
# Each entry defines: table name, SELECT from source, INSERT into target,
# and the column names used to extract parameters from source rows.

TABLE_SPECS: list[dict[str, Any]] = [
    # ── Atoms (schema differs: extra columns with defaults) ──
    {
        "table": "atoms",
        "batch_size": 500,
        "source_select": """
            SELECT atom_id, fqdn, owner_id, domain_tags, description,
                   status, superseded_by, created_at, updated_at
            FROM atoms ORDER BY created_at
        """,
        "target_insert": """
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
        "param_columns": [
            "atom_id", "fqdn", "owner_id", "domain_tags", "description",
            "status", "superseded_by", "created_at", "updated_at",
        ],
    },
    # ── Atom versions ──
    {
        "table": "atom_versions",
        "source_select": """
            SELECT version_id, atom_id, content_hash, semver, is_latest,
                   derives_from, s3_key, fingerprint, created_at
            FROM atom_versions ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.atom_versions (
                version_id, atom_id, content_hash, semver, is_latest,
                derives_from, s3_key, fingerprint, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (version_id) DO NOTHING
        """,
        "param_columns": [
            "version_id", "atom_id", "content_hash", "semver", "is_latest",
            "derives_from", "s3_key", "fingerprint", "created_at",
        ],
    },
    # ── Atom authors ──
    {
        "table": "atom_authors",
        "batch_size": 500,
        "source_select": """
            SELECT atom_id, user_id, contribution_share
            FROM atom_authors ORDER BY atom_id, user_id
        """,
        "target_insert": """
            INSERT INTO public.atom_authors (atom_id, user_id, contribution_share)
            VALUES ($1,$2,$3)
            ON CONFLICT (atom_id, user_id) DO NOTHING
        """,
        "param_columns": ["atom_id", "user_id", "contribution_share"],
    },
    # ── Hyperparams ──
    {
        "table": "hyperparams",
        "source_select": """
            SELECT hp_id, atom_id, name, kind, default_value, min_value,
                   max_value, step_value, log_scale, choices_json,
                   constraints_json, semantic_role, status
            FROM hyperparams ORDER BY hp_id
        """,
        "target_insert": """
            INSERT INTO public.hyperparams (
                hp_id, atom_id, name, kind, default_value, min_value,
                max_value, step_value, log_scale, choices_json,
                constraints_json, semantic_role, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (hp_id) DO NOTHING
        """,
        "param_columns": [
            "hp_id", "atom_id", "name", "kind", "default_value", "min_value",
            "max_value", "step_value", "log_scale", "choices_json",
            "constraints_json", "semantic_role", "status",
        ],
    },
    # ── Atom benchmarks ──
    {
        "table": "atom_benchmarks",
        "source_select": """
            SELECT benchmark_id, version_id, benchmark_name, metric_name,
                   metric_value, dataset_tag, measured_at
            FROM atom_benchmarks ORDER BY benchmark_id
        """,
        "target_insert": """
            INSERT INTO public.atom_benchmarks (
                benchmark_id, version_id, benchmark_name, metric_name,
                metric_value, dataset_tag, measured_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (benchmark_id) DO NOTHING
        """,
        "param_columns": [
            "benchmark_id", "version_id", "benchmark_name", "metric_name",
            "metric_value", "dataset_tag", "measured_at",
        ],
    },
    # ── Bounties ──
    {
        "table": "bounties",
        "batch_size": 500,
        "source_select": """
            SELECT bounty_id, principal_id, title, escrow_amount, status,
                   deadline, tier, verification_budget, verifications_used,
                   config_yml, flare_payload, ageom_yml_s3, dataset_s3,
                   public_split_hash, blind_split_hash, cancellation_fee,
                   reposted_from, created_at, updated_at
            FROM bounties ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.bounties (
                bounty_id, principal_id, title, escrow_amount, status,
                deadline, tier, verification_budget, verifications_used,
                config_yml, flare_payload, ageom_yml_s3, dataset_s3,
                public_split_hash, blind_split_hash, cancellation_fee,
                reposted_from, created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            ON CONFLICT (bounty_id) DO NOTHING
        """,
        "param_columns": [
            "bounty_id", "principal_id", "title", "escrow_amount", "status",
            "deadline", "tier", "verification_budget", "verifications_used",
            "config_yml", "flare_payload", "ageom_yml_s3", "dataset_s3",
            "public_split_hash", "blind_split_hash", "cancellation_fee",
            "reposted_from", "created_at", "updated_at",
        ],
    },
    # ── Submissions ──
    {
        "table": "submissions",
        "batch_size": 500,
        "source_select": """
            SELECT submission_id, bounty_id, architect_id, cdg_hash,
                   atom_versions, receipt_s3, receipt_json,
                   claimed_metric_name, claimed_metric_value,
                   verified_metric_value, verification_status,
                   is_winner, submitted_at, verified_at
            FROM submissions ORDER BY submitted_at
        """,
        "target_insert": """
            INSERT INTO public.submissions (
                submission_id, bounty_id, architect_id, cdg_hash,
                atom_versions, receipt_s3, receipt_json,
                claimed_metric_name, claimed_metric_value,
                verified_metric_value, verification_status,
                is_winner, submitted_at, verified_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (submission_id) DO NOTHING
        """,
        "param_columns": [
            "submission_id", "bounty_id", "architect_id", "cdg_hash",
            "atom_versions", "receipt_s3", "receipt_json",
            "claimed_metric_name", "claimed_metric_value",
            "verified_metric_value", "verification_status",
            "is_winner", "submitted_at", "verified_at",
        ],
    },
    # ── Payouts ──
    {
        "table": "payouts",
        "batch_size": 500,
        "source_select": """
            SELECT payout_id, bounty_id, user_id, role, amount,
                   shapley_value, stripe_transfer_id, status, created_at
            FROM payouts ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.payouts (
                payout_id, bounty_id, user_id, role, amount,
                shapley_value, stripe_transfer_id, status, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (payout_id) DO NOTHING
        """,
        "param_columns": [
            "payout_id", "bounty_id", "user_id", "role", "amount",
            "shapley_value", "stripe_transfer_id", "status", "created_at",
        ],
    },
    # ── Verification budgets ──
    {
        "table": "verification_budgets",
        "source_select": """
            SELECT bounty_id, tier, total_slots, used_slots,
                   cost_per_extra, overhead_deposit, overhead_used, created_at
            FROM verification_budgets ORDER BY bounty_id
        """,
        "target_insert": """
            INSERT INTO public.verification_budgets (
                bounty_id, tier, total_slots, used_slots,
                cost_per_extra, overhead_deposit, overhead_used, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (bounty_id) DO NOTHING
        """,
        "param_columns": [
            "bounty_id", "tier", "total_slots", "used_slots",
            "cost_per_extra", "overhead_deposit", "overhead_used", "created_at",
        ],
    },
    # ── Verification runs ──
    {
        "table": "verification_runs",
        "source_select": """
            SELECT id, bounty_id, submission_id, split_type, status,
                   metric_values, output_hash, execution_time_s,
                   peak_memory_bytes, is_deterministic, sandbox_job_id,
                   slot_consumed, started_at, completed_at, created_at
            FROM verification_runs ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.verification_runs (
                id, bounty_id, submission_id, split_type, status,
                metric_values, output_hash, execution_time_s,
                peak_memory_bytes, is_deterministic, sandbox_job_id,
                slot_consumed, started_at, completed_at, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (id) DO NOTHING
        """,
        "param_columns": [
            "id", "bounty_id", "submission_id", "split_type", "status",
            "metric_values", "output_hash", "execution_time_s",
            "peak_memory_bytes", "is_deterministic", "sandbox_job_id",
            "slot_consumed", "started_at", "completed_at", "created_at",
        ],
    },
    # ── Bounty best scores ──
    {
        "table": "bounty_best_scores",
        "source_select": """
            SELECT bounty_id, metric_name, best_value,
                   best_submission_id, is_baseline, updated_at
            FROM bounty_best_scores ORDER BY bounty_id, metric_name
        """,
        "target_insert": """
            INSERT INTO public.bounty_best_scores (
                bounty_id, metric_name, best_value,
                best_submission_id, is_baseline, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (bounty_id, metric_name) DO NOTHING
        """,
        "param_columns": [
            "bounty_id", "metric_name", "best_value",
            "best_submission_id", "is_baseline", "updated_at",
        ],
    },
    # ── Principal targets ──
    {
        "table": "principal_targets",
        "source_select": """
            SELECT id, bounty_id, metric_name, target_value, set_by, created_at
            FROM principal_targets ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.principal_targets (
                id, bounty_id, metric_name, target_value, set_by, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING
        """,
        "param_columns": [
            "id", "bounty_id", "metric_name", "target_value", "set_by", "created_at",
        ],
    },
    # ── Execution receipts ──
    {
        "table": "execution_receipts",
        "batch_size": 500,
        "source_select": """
            SELECT id, submission_id, bounty_id, cdg_hash, atom_versions,
                   split_hash, output_hash, metric_name, metric_value,
                   ageom_version, ssh_signature, ssh_public_key,
                   verified, receipt_timestamp, created_at
            FROM execution_receipts ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.execution_receipts (
                id, submission_id, bounty_id, cdg_hash, atom_versions,
                split_hash, output_hash, metric_name, metric_value,
                ageom_version, ssh_signature, ssh_public_key,
                verified, receipt_timestamp, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (id) DO NOTHING
        """,
        "param_columns": [
            "id", "submission_id", "bounty_id", "cdg_hash", "atom_versions",
            "split_hash", "output_hash", "metric_name", "metric_value",
            "ageom_version", "ssh_signature", "ssh_public_key",
            "verified", "receipt_timestamp", "created_at",
        ],
    },
    # ── Dataset splits ──
    {
        "table": "dataset_splits",
        "source_select": """
            SELECT id, bounty_id, unit_key, partition, created_at
            FROM dataset_splits ORDER BY bounty_id, unit_key
        """,
        "target_insert": """
            INSERT INTO public.dataset_splits (
                id, bounty_id, unit_key, partition, created_at
            ) VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (id) DO NOTHING
        """,
        "param_columns": [
            "id", "bounty_id", "unit_key", "partition", "created_at",
        ],
    },
    # ── Settlement payouts ──
    {
        "table": "settlement_payouts",
        "source_select": """
            SELECT id, bounty_id, recipient_id, role, amount,
                   stripe_transfer_id, atom_fqdn, cdg_hash, created_at
            FROM settlement_payouts ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.settlement_payouts (
                id, bounty_id, recipient_id, role, amount,
                stripe_transfer_id, atom_fqdn, cdg_hash, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (id) DO NOTHING
        """,
        "param_columns": [
            "id", "bounty_id", "recipient_id", "role", "amount",
            "stripe_transfer_id", "atom_fqdn", "cdg_hash", "created_at",
        ],
    },
    # ── Benchmark suites ──
    {
        "table": "benchmark_suites",
        "source_select": """
            SELECT benchmark_id, domain_tags, description, dataset_s3_key,
                   metric_names, curation_source, proposer_id, vote_count,
                   status, created_at
            FROM benchmark_suites ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.benchmark_suites (
                benchmark_id, domain_tags, description, dataset_s3_key,
                metric_names, curation_source, proposer_id, vote_count,
                status, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (benchmark_id) DO NOTHING
        """,
        "param_columns": [
            "benchmark_id", "domain_tags", "description", "dataset_s3_key",
            "metric_names", "curation_source", "proposer_id", "vote_count",
            "status", "created_at",
        ],
    },
    # ── Benchmark votes ──
    {
        "table": "benchmark_votes",
        "source_select": """
            SELECT benchmark_id, voter_id, vote, created_at
            FROM benchmark_votes ORDER BY benchmark_id, voter_id
        """,
        "target_insert": """
            INSERT INTO public.benchmark_votes (
                benchmark_id, voter_id, vote, created_at
            ) VALUES ($1,$2,$3,$4)
            ON CONFLICT (benchmark_id, voter_id) DO NOTHING
        """,
        "param_columns": [
            "benchmark_id", "voter_id", "vote", "created_at",
        ],
    },
    # ── Fuzz results ──
    {
        "table": "fuzz_results",
        "source_select": """
            SELECT fuzz_id, atom_fqdn, content_hash, strategy, passed,
                   failures, inputs_tested, runtime_ms, created_at
            FROM fuzz_results ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.fuzz_results (
                fuzz_id, atom_fqdn, content_hash, strategy, passed,
                failures, inputs_tested, runtime_ms, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (fuzz_id) DO NOTHING
        """,
        "param_columns": [
            "fuzz_id", "atom_fqdn", "content_hash", "strategy", "passed",
            "failures", "inputs_tested", "runtime_ms", "created_at",
        ],
    },
    # ── Behavioral equivalence flags ──
    {
        "table": "behavioral_equivalence_flags",
        "source_select": """
            SELECT flag_id, atom_a_fqdn, atom_a_hash, atom_b_fqdn, atom_b_hash,
                   match_ratio, sample_size, reviewed, reviewer_id,
                   disposition, created_at
            FROM behavioral_equivalence_flags ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.behavioral_equivalence_flags (
                flag_id, atom_a_fqdn, atom_a_hash, atom_b_fqdn, atom_b_hash,
                match_ratio, sample_size, reviewed, reviewer_id,
                disposition, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (flag_id) DO NOTHING
        """,
        "param_columns": [
            "flag_id", "atom_a_fqdn", "atom_a_hash", "atom_b_fqdn", "atom_b_hash",
            "match_ratio", "sample_size", "reviewed", "reviewer_id",
            "disposition", "created_at",
        ],
    },
    # ── Discipline repos ──
    {
        "table": "discipline_repos",
        "source_select": """
            SELECT repo_id, repo_url, webhook_secret, domain_tags,
                   maintainer_ids, last_synced_commit, status,
                   created_at, updated_at
            FROM discipline_repos ORDER BY created_at
        """,
        "target_insert": """
            INSERT INTO public.discipline_repos (
                repo_id, repo_url, webhook_secret, domain_tags,
                maintainer_ids, last_synced_commit, status,
                created_at, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (repo_id) DO NOTHING
        """,
        "param_columns": [
            "repo_id", "repo_url", "webhook_secret", "domain_tags",
            "maintainer_ids", "last_synced_commit", "status",
            "created_at", "updated_at",
        ],
    },
]


# ---------------------------------------------------------------------------
# Migration engine
# ---------------------------------------------------------------------------

async def migrate_table(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    spec: dict[str, Any],
) -> int:
    """Migrate a single table. Returns the number of rows migrated."""
    table = spec["table"]
    batch_size = spec.get("batch_size", BATCH_SIZE)

    rows = await src.fetch(spec["source_select"])
    if not rows:
        log.info("[%s] 0 rows (empty source)", table)
        return 0

    total = len(rows)
    migrated = 0

    for i in range(0, total, batch_size):
        batch = rows[i : i + batch_size]
        params = [
            tuple(dict(row)[col] for col in spec["param_columns"])
            for row in batch
        ]

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await dst.executemany(spec["target_insert"], params)
                migrated += len(batch)
                break
            except Exception as exc:
                if attempt < MAX_RETRIES:
                    log.warning(
                        "[%s] batch %d-%d failed (attempt %d/%d): %s",
                        table, i + 1, i + len(batch), attempt, MAX_RETRIES, exc,
                    )
                    await asyncio.sleep(RETRY_DELAY_S * attempt)
                else:
                    raise RuntimeError(
                        f"[{table}] batch {i+1}-{i+len(batch)} failed after "
                        f"{MAX_RETRIES} attempts"
                    ) from exc

        log.info(
            "[%s] %d-%d / %d",
            table, i + 1, min(i + batch_size, total), total,
        )

    log.info("[%s] migrated %d / %d rows", table, migrated, total)
    return migrated


# FK-respecting migration order.
MIGRATION_ORDER = [
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
]


async def main(tables: list[str] | None = None) -> None:
    target_tables = tables or MIGRATION_ORDER
    spec_map = {s["table"]: s for s in TABLE_SPECS}

    src = await asyncpg.connect(SOURCE_DATABASE_URL)
    dst = await asyncpg.connect(SUPABASE_DATABASE_URL)

    try:
        for table_name in target_tables:
            spec = spec_map.get(table_name)
            if spec is None:
                log.error("No spec for table '%s', skipping", table_name)
                continue
            await migrate_table(src, dst, spec)
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    requested = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(main(requested))
```

---

## 6. Batch Sizing Strategy

| Table category | Recommended batch size | Rationale |
|---|---|---|
| Wide rows with JSONB (bounties, submissions, execution_receipts) | 500 | Larger payloads per row; stay under memory thresholds |
| Narrow rows (atom_authors, benchmark_votes, dataset_splits) | 1,000 | Small payloads, maximize throughput |
| Auth Admin API (users) | 500 per batch, sequential API calls | Supabase Admin API rate limit ~30 req/s; sequential within batch |
| Default | 1,000 | Good balance of throughput vs. memory |

General rules:
- Use 500 for tables with JSONB columns or wide rows to stay under `work_mem`.
- Use 1,000 for narrow tables.
- Each batch is committed independently (no wrapping transaction) so that
  partial failures do not roll back already-committed batches.
- ON CONFLICT DO NOTHING on all inserts means re-runs skip already-migrated rows.

---

## 7. Validation

### Script: `scripts/validate_phase1.py`

```python
"""Phase 1 validation: compare row counts and checksums between source and target.

Usage:
    python scripts/validate_phase1.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SOURCE_DATABASE_URL = os.environ["SOURCE_DATABASE_URL"]
SUPABASE_DATABASE_URL = os.environ["SUPABASE_DATABASE_URL"]

# (table_name, checksum_columns)
# Checksum is computed as MD5 of concatenated column values across all rows,
# ordered by the first column. This catches row-level data mismatches.
VALIDATION_TABLES = [
    ("users", ["user_id", "github_id", "github_login", "email", "reputation_score"]),
    ("atoms", ["atom_id", "fqdn", "owner_id", "status"]),
    ("atom_versions", ["version_id", "atom_id", "content_hash", "semver"]),
    ("atom_authors", ["atom_id", "user_id", "contribution_share"]),
    ("hyperparams", ["hp_id", "atom_id", "name", "kind"]),
    ("atom_benchmarks", ["benchmark_id", "version_id", "metric_value"]),
    ("bounties", ["bounty_id", "principal_id", "title", "escrow_amount", "status"]),
    ("submissions", ["submission_id", "bounty_id", "architect_id", "verification_status"]),
    ("payouts", ["payout_id", "bounty_id", "user_id", "amount", "status"]),
    ("verification_budgets", ["bounty_id", "tier", "total_slots", "used_slots"]),
    ("verification_runs", ["id", "bounty_id", "submission_id", "status"]),
    ("bounty_best_scores", ["bounty_id", "metric_name", "best_value"]),
    ("principal_targets", ["id", "bounty_id", "metric_name", "target_value"]),
    ("execution_receipts", ["id", "submission_id", "bounty_id", "metric_value"]),
    ("dataset_splits", ["id", "bounty_id", "unit_key", "partition"]),
    ("settlement_payouts", ["id", "bounty_id", "recipient_id", "amount"]),
    ("benchmark_suites", ["benchmark_id", "curation_source", "status"]),
    ("benchmark_votes", ["benchmark_id", "voter_id", "vote"]),
    ("fuzz_results", ["fuzz_id", "atom_fqdn", "strategy", "passed"]),
    ("behavioral_equivalence_flags", ["flag_id", "atom_a_fqdn", "atom_b_fqdn", "match_ratio"]),
    ("discipline_repos", ["repo_id", "repo_url", "status"]),
]


async def compute_checksum(
    conn: asyncpg.Connection, table: str, columns: list[str]
) -> str:
    """MD5 checksum over all rows of the given columns, ordered deterministically."""
    cols_expr = " || '|' || ".join(f"COALESCE({c}::text, '')" for c in columns)
    query = (
        f"SELECT md5(string_agg({cols_expr}, E'\\n' ORDER BY {columns[0]}::text)) "
        f"FROM {table}"
    )
    return await conn.fetchval(query) or "<empty>"


async def validate_table(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    table: str,
    checksum_cols: list[str],
) -> dict:
    src_count = await src.fetchval(f"SELECT count(*) FROM {table}")
    dst_count = await dst.fetchval(f"SELECT count(*) FROM public.{table}")

    count_match = src_count == dst_count

    # Only compute checksums if counts match (saves time on obvious failures).
    if count_match:
        src_checksum = await compute_checksum(src, table, checksum_cols)
        dst_checksum = await compute_checksum(dst, f"public.{table}", checksum_cols)
        checksum_match = src_checksum == dst_checksum
    else:
        src_checksum = dst_checksum = "<skipped>"
        checksum_match = False

    passed = count_match and checksum_match
    status = "PASS" if passed else "FAIL"
    log.info(
        "%s %-35s src=%-6d dst=%-6d count=%s checksum=%s",
        status, table, src_count, dst_count,
        "ok" if count_match else "MISMATCH",
        "ok" if checksum_match else "MISMATCH",
    )
    return {
        "table": table,
        "src_count": src_count,
        "dst_count": dst_count,
        "count_match": count_match,
        "checksum_match": checksum_match,
        "passed": passed,
    }


async def validate_fk_integrity(dst: asyncpg.Connection) -> list[dict]:
    """Run FK integrity spot-checks on the target database."""
    checks = [
        (
            "atoms.owner_id -> users",
            """SELECT count(*) FROM public.atoms a
               LEFT JOIN public.users u ON u.user_id = a.owner_id
               WHERE u.user_id IS NULL""",
        ),
        (
            "atom_versions.atom_id -> atoms",
            """SELECT count(*) FROM public.atom_versions av
               LEFT JOIN public.atoms a ON a.atom_id = av.atom_id
               WHERE a.atom_id IS NULL""",
        ),
        (
            "atom_authors.user_id -> users",
            """SELECT count(*) FROM public.atom_authors aa
               LEFT JOIN public.users u ON u.user_id = aa.user_id
               WHERE u.user_id IS NULL""",
        ),
        (
            "bounties.principal_id -> users",
            """SELECT count(*) FROM public.bounties b
               LEFT JOIN public.users u ON u.user_id = b.principal_id
               WHERE u.user_id IS NULL""",
        ),
        (
            "submissions.bounty_id -> bounties",
            """SELECT count(*) FROM public.submissions s
               LEFT JOIN public.bounties b ON b.bounty_id = s.bounty_id
               WHERE b.bounty_id IS NULL""",
        ),
        (
            "auth.users <-> public.users 1:1",
            """SELECT count(*) FROM auth.users au
               LEFT JOIN public.users pu ON pu.user_id = au.id
               WHERE pu.user_id IS NULL""",
        ),
    ]
    results = []
    for name, query in checks:
        orphans = await dst.fetchval(query)
        passed = orphans == 0
        status = "PASS" if passed else "FAIL"
        log.info("%s FK %-40s orphans=%d", status, name, orphans)
        results.append({"check": name, "orphans": orphans, "passed": passed})
    return results


async def main() -> None:
    src = await asyncpg.connect(SOURCE_DATABASE_URL)
    dst = await asyncpg.connect(SUPABASE_DATABASE_URL)

    results = []
    fk_results = []
    try:
        log.info("=" * 70)
        log.info("Row count and checksum validation")
        log.info("=" * 70)
        for table, cols in VALIDATION_TABLES:
            try:
                result = await validate_table(src, dst, table, cols)
                results.append(result)
            except Exception as exc:
                log.error("Validation error for %s: %s", table, exc)
                results.append({"table": table, "passed": False, "error": str(exc)})

        log.info("")
        log.info("=" * 70)
        log.info("FK integrity checks")
        log.info("=" * 70)
        fk_results = await validate_fk_integrity(dst)
    finally:
        await src.close()
        await dst.close()

    # Summary
    table_passed = sum(1 for r in results if r.get("passed"))
    table_failed = len(results) - table_passed
    fk_passed = sum(1 for r in fk_results if r.get("passed"))
    fk_failed = len(fk_results) - fk_passed

    log.info("")
    log.info("=" * 70)
    log.info("SUMMARY")
    log.info("=" * 70)
    log.info("Tables:  %d passed, %d failed, %d total", table_passed, table_failed, len(results))
    log.info("FK:      %d passed, %d failed, %d total", fk_passed, fk_failed, len(fk_results))

    if table_failed > 0 or fk_failed > 0:
        log.error("MIGRATION VALIDATION FAILED")
        for r in results:
            if not r.get("passed"):
                log.error("  FAILED: %s", json.dumps(r, default=str))
        for r in fk_results:
            if not r.get("passed"):
                log.error("  FAILED FK: %s", json.dumps(r, default=str))
        sys.exit(1)
    else:
        log.info("All validations passed.")


if __name__ == "__main__":
    asyncio.run(main())
```

### Manual spot-check queries

Run these on the Supabase database after migration:

```sql
-- auth.users <-> public.users 1:1 check
SELECT
    (SELECT count(*) FROM auth.users) AS auth_count,
    (SELECT count(*) FROM public.users) AS profile_count;
-- Expected: both equal

-- Verify handle_new_user trigger is active
SELECT tgname, tgenabled
FROM pg_trigger
WHERE tgname = 'on_auth_user_created';
-- Expected: tgenabled = 'O' (origin/always)

-- UUID preservation spot-check (compare 5 atoms between old PG and Supabase)
-- Old PG:  SELECT atom_id, fqdn FROM atoms ORDER BY created_at LIMIT 5;
-- Supabase: verify same atom_ids exist with same fqdns
SELECT atom_id, fqdn FROM public.atoms
WHERE atom_id IN ('<id1>', '<id2>', '<id3>', '<id4>', '<id5>');
```

---

## 8. Error Handling and Retry Logic

### Per-batch retry

Every batch insert is wrapped in a retry loop:

1. Attempt the batch insert via `asyncpg.executemany`.
2. On failure, log the error with table name, batch range, and exception details.
3. Wait `RETRY_DELAY_S * attempt` seconds (exponential backoff: 2s, 4s, 6s).
4. Retry up to `MAX_RETRIES` times (default: 3).
5. If all retries fail, raise a `RuntimeError` with full context. The script
   exits with a non-zero status code.

### Idempotency

All INSERT statements use `ON CONFLICT ... DO NOTHING`. This means:

- The migration can be re-run safely after a partial failure.
- Rows that were already inserted in a previous run are silently skipped.
- No data is overwritten or duplicated.
- No TRUNCATE is needed before a re-run.

### Auth API error handling

The `create_auth_user` function handles specific failure modes:

| Status code | Handling |
|---|---|
| 200, 201 | Success |
| 422 "already been registered" | Treated as success (idempotent) |
| 429 (rate limit) | Read `Retry-After` header, sleep, retry |
| Other 4xx/5xx | Log, backoff, retry up to MAX_RETRIES |

### Transaction boundaries

Each batch is committed independently -- `asyncpg.executemany` auto-commits
outside an explicit transaction. This is intentional: if batch N fails, batches
1..N-1 are already persisted and will be skipped on re-run thanks to
`ON CONFLICT DO NOTHING`.

For tables where partial migration would leave FK violations (e.g., `atoms`
must be fully loaded before `atom_versions`), the migration order in
`MIGRATION_ORDER` ensures parent tables are loaded first.

### Logging

Every batch logs: table name, batch range (start-end / total), and success or
failure with exception details. The validation script logs per-table pass/fail
with counts and checksums, FK integrity results, and a summary.

---

## 9. Rollback Procedure

### Key property: source database is untouched

Phase 1 only reads from the source database. It never writes to it. The source
database remains the production system throughout Phase 1. Rollback means
undoing only the Supabase-side changes.

### Option A: Truncate and re-run (partial failure)

If the migration fails partway through, truncate all Supabase tables and re-run
from the beginning:

```sql
-- Disable triggers to avoid cascading issues during truncation.
SET session_replication_role = 'replica';

-- Phase D / Ecosystem
TRUNCATE public.discipline_repos CASCADE;
TRUNCATE public.behavioral_equivalence_flags CASCADE;
TRUNCATE public.fuzz_results CASCADE;
TRUNCATE public.benchmark_votes CASCADE;
TRUNCATE public.benchmark_suites CASCADE;

-- Phase C / Verification
TRUNCATE public.settlement_payouts CASCADE;
TRUNCATE public.dataset_splits CASCADE;
TRUNCATE public.execution_receipts CASCADE;
TRUNCATE public.principal_targets CASCADE;
TRUNCATE public.bounty_best_scores CASCADE;
TRUNCATE public.verification_runs CASCADE;
TRUNCATE public.verification_budgets CASCADE;

-- Core bounty
TRUNCATE public.payouts CASCADE;
TRUNCATE public.submissions CASCADE;
TRUNCATE public.bounties CASCADE;

-- Atom registry
TRUNCATE public.atom_benchmarks CASCADE;
TRUNCATE public.hyperparams CASCADE;
TRUNCATE public.atom_authors CASCADE;
TRUNCATE public.atom_versions CASCADE;
TRUNCATE public.atoms CASCADE;

-- Organizations
TRUNCATE public.organization_memberships CASCADE;
TRUNCATE public.organization_email_domains CASCADE;
TRUNCATE public.organizations CASCADE;

-- Users (public profile + auth)
TRUNCATE public.users CASCADE;
DELETE FROM auth.users;

SET session_replication_role = 'origin';
```

Then re-run the migration scripts from step 1.

### Option B: Full abort

If Phase 1 is completely unviable, the old PG is still the production database.
No application code has been modified. Either drop all Phase 1 tables or reset
the Supabase project entirely from the dashboard.

### Point of no return

Phase 1 is fully reversible. The point of no return is Phase 3 (dual-write),
when the application starts writing to both databases simultaneously.

---

## 10. Execution Order

```bash
# 1. Migrate users (creates auth.users + public.users)
export SOURCE_DATABASE_URL="postgres://..."
export SUPABASE_DATABASE_URL="postgres://..."
export SUPABASE_URL="https://xxx.supabase.co"
export SUPABASE_SERVICE_KEY="eyJ..."

python scripts/migrate_phase1_users.py

# 2. Seed organizations and auto-assign memberships
python scripts/migrate_phase1_organizations.py

# 3. Migrate all remaining tables (respects FK order internally)
python scripts/migrate_phase1_tables.py

# 4. Validate everything
python scripts/validate_phase1.py
```

To migrate specific tables only (e.g., after fixing a failure):

```bash
python scripts/migrate_phase1_tables.py atoms atom_versions
```

### Estimated duration

With ~500 users, ~500 atoms, and ~5,000 total rows across all tables:

| Step | Duration |
|---|---|
| User migration (rate-limited by Auth API) | 2-5 min |
| Organization seeding | < 10 sec |
| Table migration (20 tables) | 1-2 min |
| Validation | 30 sec |
| **Total** | **5-10 min** |

---

## 11. Post-Migration Checklist

- [ ] `validate_phase1.py` exits with code 0 (all tables and FK checks pass)
- [ ] `auth.users` count matches `public.users` count
- [ ] `handle_new_user` trigger is re-enabled on `auth.users`
- [ ] New user signup via Supabase Auth creates a `public.users` row correctly
      (test with a throwaway GitHub account)
- [ ] Source database is unchanged (spot-check row counts match pre-migration)
- [ ] Migration scripts and validation output are saved for audit trail
- [ ] Proceed to Phase 2 sub-plans:
  - Phase 2b: audit evidence and rollups from `audit_manifest.json`
  - Phase 2c: references from `references.json` files
  - Phase 2d: uncertainty estimates and verification matches from JSON files
  - Phase 2e: intrinsic fields, source repositories, roles, entitlements
