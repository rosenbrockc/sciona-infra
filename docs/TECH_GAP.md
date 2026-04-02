# Technical Gap Analysis

Updated repository audit for the Supabase migration as of 2026-03-31.

This replaces the older architecture-first gap writeup. The old document assumed
custom JWT auth, asyncpg-backed API handlers, `/catalog/manifest`, and a future
Supabase migration. The repository no longer matches those assumptions.

Scope of this audit:

- current code under `sciona/`
- current Supabase migrations under `supabase/`
- migration/backfill scripts under `scripts/`
- current test coverage in `tests/`
- the more detailed target state in [`docs/SUPABASE_MIGRATION_PLAN.md`](./SUPABASE_MIGRATION_PLAN.md)

## Executive Summary

- Supabase is already the primary runtime integration for the API and catalog
  sync path. The old asyncpg/JWT runtime path is effectively gone.
- Phase 0, Phase 3, major parts of Phase 4, and the database side of Phase 6
  are implemented in-repo.
- The highest-risk remaining gap is that normal API dependencies currently
  prefer the Supabase service-role client, which can bypass the RLS model the
  migration plan is built around.
- The second major gap is completeness: the registry publish path still writes
  only the minimal `atoms` + `atom_versions` records, not the richer
  documentation bundle required by the plan.
- The biggest document drift is Phase 5. The repo has already removed the
  dual-write wrapper and asyncpg API dependency, so the plan should no longer be
  read as "future staged cutover"; it needs to be rebased around hardening the
  current Supabase-first implementation.

## Audit Basis

### Reviewed implementation areas

- `sciona/api/app.py`
- `sciona/api/deps.py`
- `sciona/api/routers/auth.py`
- `sciona/api/routers/catalog.py`
- `sciona/api/routers/registry.py`
- `sciona/api/routers/bounty.py`
- `sciona/api/routers/verification.py`
- `sciona/api/routers/dashboard.py`
- `sciona/api/snapshot.py`
- `sciona/commands/catalog_cmds.py`
- `sciona/commands/login_cmds.py`
- `supabase/migrations/20260331000100_phase0_foundation_schema.sql`
- `supabase/migrations/20260331000200_phase3_triggers_rls_views.sql`
- `supabase/migrations/20260331000300_phase4_client_rpcs.sql`
- `supabase/migrations/20260331000400_phase6_search_and_embeddings.sql`
- Phase 1 / Phase 2 / Phase 6 migration scripts under `scripts/`

### Verification notes

- A focused test run under the repo venv reached `84 passed`, but `17` async
  Supabase tests did not execute because that venv is missing `pytest-asyncio`.
- The default system/anaconda pytest environment includes `pytest-asyncio`, but
  uses Python 3.10 and fails earlier because `tests/conftest.py` imports
  `tomllib`.
- Result: helper and script coverage exists, but this repo does not currently
  provide one clean local environment that can execute the full focused
  Supabase test set end-to-end.

## Current Status By Phase

| Area | Status | Repo Evidence | Audit Notes |
|---|---|---|---|
| Phase 0 foundation schema | Implemented | `supabase/migrations/20260331000100_phase0_foundation_schema.sql`, `supabase/seed.sql`, `supabase/sql/phase0_validation.sql` | Core schema, helper functions, seed roles, and validation SQL are present. |
| Phase 1 core data migration | Partial | `scripts/migrate_phase1_users.py`, `scripts/migrate_phase1_tables.py`, `scripts/migrate_phase1_organizations.py`, `scripts/phase1_validate.py` | Migration and validation tooling exists, but there is no single in-repo orchestration path proving full operational completion. |
| Phase 2A/2B/2C/2D/2E backfills | Partial | `scripts/backfill_*`, `supabase/sql/phase2*` | Backfill helpers are present and tested, but the runtime API does not yet write the rich model directly. |
| Phase 3 triggers/RLS/views | Implemented | `supabase/migrations/20260331000200_phase3_triggers_rls_views.sql`, `tests/test_supabase_local_integration.py` | The DB-side visibility and document-serving model is present. |
| Phase 4 client/API migration | Mostly implemented | `sciona/api/*`, `sciona/api/snapshot.py`, `sciona/commands/catalog_cmds.py` | API handlers and manifest sync now use Supabase. Some plan details are still missing or inconsistent. |
| Phase 5 dual-write/cutover | Stale plan / not implemented as written | `sciona/api/dual_write.py` deleted, no `get_db`, no asyncpg pool in API app | The repo has already moved past the plan's staged dual-write shape. |
| Phase 6 search/embeddings | Partial | `supabase/migrations/20260331000400_phase6_search_and_embeddings.sql`, `scripts/generate_embeddings.py` | DB objects and worker script exist; HTTP/API exposure and ops are still incomplete. |

## What Is Implemented Now

### 1. Supabase-backed API runtime

Implemented:

- `sciona/api/app.py` creates Supabase public/admin clients from environment.
- `sciona/api/deps.py` validates bearer tokens through Supabase Auth and loads
  `public.users`.
- `auth`, `catalog`, `registry`, `bounty`, `verification`, and `dashboard`
  routers all use Supabase query builder / RPC calls rather than asyncpg.
- There is no remaining runtime `get_db` / `db_pool` path in the API.
- `PyJWT` is no longer part of the runtime code path.

Implication:

- The migration is not hypothetical anymore. Supabase is already the active API
  integration model in code.

### 2. Supabase-backed catalog cache generation

Implemented:

- `sciona/api/snapshot.py` fetches atoms, hyperparams, audit rollups,
  descriptions, and benchmarks from Supabase.
- `sciona/commands/catalog_cmds.py` builds `~/.sciona/manifest.sqlite` locally
  from Supabase data.
- `load_hyperparams_manifest_sqlite()` and benchmark readers remain compatible
  with the generated SQLite shape.
- `/catalog/manifest` is gone from the API router.

Implication:

- The plan's "SQLite remains as a cache, not source of truth" direction has
  landed in practice.

### 3. Database-side visibility, serving, and search primitives

Implemented:

- Materialized access model: `users.effective_tier` and `atoms.is_publishable`
- RLS policies across users, atoms, documentation tables, bounty tables, and
  ecosystem tables
- Served catalog/document views and RPCs:
  - `catalog_atoms_served`
  - `get_atom_document(...)`
  - `get_manifest_benchmarks()`
  - `get_originator_impact(...)`
  - `get_bounty_leaderboard(...)`
- Search/embedding DB objects:
  - `catalog_atoms_index`
  - `search_atoms_fts(...)`
  - `search_atoms_vector(...)`
  - `search_atoms_hybrid(...)`
  - `atom_embeddings`
  - `embedding_refresh_queue`

Implication:

- The repository already contains the database contract that the plan describes.

## Remaining Gaps

## 1. Critical

### 1.1 RLS can be bypassed by the current dependency wiring

Current state:

- `sciona/api/deps.py:get_supabase()` returns `supabase_admin` first, then
  `supabase`.
- If a service-role key is configured, normal request handlers will use the
  admin client rather than the anon/authenticated client.

Why this matters:

- The migration plan is explicitly built around RLS being authoritative.
- Service-role access bypasses the policy layer the rest of the schema was
  designed around.
- This weakens confidence in all visibility-tier, ownership, and entitlement
  assumptions.

Required resolution:

- Decide whether request-path reads/writes must run under end-user/RLS context.
- If yes, route normal handlers through the public client and reserve the admin
  client for explicit server-side maintenance tasks only.

### 1.2 The richer atom-document model is not written on publish

Current state:

- `sciona/api/routers/registry.py:publish_atom()` inserts only:
  - `atoms`
  - `atom_versions`
- The request model includes `authors`, but the handler does not write
  `atom_authors`.
- The handler does not insert:
  - `atom_io_specs`
  - `atom_parameters`
  - `atom_descriptions`
  - `atom_references`
  - `atom_audit_rollups`
  - `atom_uncertainty_estimates`
  - `atom_verification_matches`

Why this matters:

- The detailed migration plan treats the rich document model as first-class,
  queryable catalog state.
- Today the runtime publish path still depends on later backfills or out-of-band
  admin jobs to make an atom fully publishable.

Required resolution:

- Either extend publish/update endpoints to populate the full document bundle,
  or explicitly declare that API publish is intentionally minimal and a separate
  ingestion pipeline remains canonical.

### 1.3 Phase 5 is no longer an accurate description of the repo

Current state:

- `sciona/api/dual_write.py` is deleted.
- API handlers no longer use asyncpg or a dual-write wrapper.
- `get_db` / `db_pool` no longer exist in the runtime API.

Why this matters:

- The plan still reads like a future migration with dual-write, read cutover,
  and full cutover stages.
- The codebase is already in a post-cutover shape.

Required resolution:

- Rewrite Phase 5 as "hardening and cleanup after direct Supabase cutover", or
  explicitly state that the staged dual-write design was superseded.

## 2. High

### 2.1 Search API does not expose the full Phase 6 surface

Current state:

- The DB supports FTS, vector, and hybrid search.
- `sciona/api/routers/catalog.py` always calls `search_atoms_hybrid(...)` with
  `mode="fts"` and never accepts an API search mode or embedding input.

Why this matters:

- Phase 6 is partly implemented only at the database layer.
- The HTTP API does not yet let clients choose `fts | vector | hybrid`.

Required resolution:

- Decide whether vector/hybrid search is part of the public API now or still
  internal-only.

### 2.2 CLI auth path is still mixed between legacy device flow and Supabase OAuth

Current state:

- Browser login endpoint exists: `GET /auth/login`
- Legacy GitHub device flow still exists:
  - `GET /auth/github/device`
  - `POST /auth/github/device/poll`
- The device-flow path still depends on `GITHUB_OAUTH_CLIENT_ID`.

Why this matters:

- The plan moved toward Supabase Auth as the canonical identity layer.
- The repo still supports a hybrid model: direct GitHub device flow followed by
  Supabase `sign_in_with_id_token(...)`.
- The plan itself flagged metadata/profile bootstrap as an open caveat for this
  path.

Required resolution:

- Decide whether the device flow is staying long-term.
- If it stays, confirm that new users created through this path reliably produce
  the required `public.users` profile row and metadata.

### 2.3 The bounty API surface is only partially reconciled with the schema

Current state:

- `BountyCreateRequest` still accepts `domain_tags`.
- `sciona/api/routers/bounty.py` does not persist them because the current
  `bounties` schema has no such column.
- `list_bounties()` accepts `domain_tag` but explicitly does not filter on it.

Why this matters:

- This is API/schema drift inside the current implementation.
- It is not directly a Supabase problem, but it is part of the repo's current
  technical gap.

Required resolution:

- Remove these fields from the API, or add the missing schema/storage model.

### 2.4 Atom source storage is still placeholder-only in the API

Current state:

- `publish_atom()` computes a content hash and writes a synthetic
  `s3_key = atoms/<hash>.tar.gz`.
- The handler does not upload the source tarball anywhere.

Why this matters:

- The schema and broader platform design assume durable atom artifact storage.
- The current API record shape implies storage that the handler does not perform.

Required resolution:

- Either add the upload path, or mark publish as metadata-only and handle source
  artifact storage in a separate ingestion service.

## 3. Medium

### 3.1 Backfill tooling exists, but the end-to-end operational story is incomplete

Current state:

- Phase 1 migration scripts exist.
- Phase 2 backfill scripts exist.
- Validation SQL and validation scripts exist.

Missing:

- one documented repo-local command path that performs the full migration in the
  intended order
- one CI or operator workflow that proves the sequence is reproducible
- one completion marker showing whether the backfills are considered finished

### 3.2 Embedding generation is implemented as a script, not an operational service

Current state:

- `scripts/generate_embeddings.py` can backfill and drain the queue.

Missing:

- scheduler / worker deployment shape
- retry/backoff / dead-letter operational ownership
- matview refresh strategy tied to production changes

### 3.3 Local verification environment is not yet clean

Current state:

- Focused Supabase tests exist, including local Supabase integration coverage.

Missing:

- a single documented environment where:
  - Python version is new enough for `tomllib`
  - async pytest plugin is installed
  - focused Supabase tests run without interpreter/plugin mismatch

### 3.4 Minor UX/doc drift remains in CLI messaging

Examples:

- `sciona catalog sync` help text still says "Download latest manifest.sqlite"
  even though the command now builds it locally from Supabase.
- `sciona login` still describes platform authentication via GitHub device flow
  rather than clarifying the current mixed Supabase/GitHub path.

## Open Questions That Still Need Answers

1. Is RLS actually intended to be the production enforcement layer, or is the
   service-role client intentionally meant to front all request-path DB access?

2. What is the canonical write path for rich atom documentation?
   Is it:
   - API publish/update time,
   - asynchronous post-publish enrichment,
   - repo-ingestion backfill only,
   - or some combination?

3. Is the legacy device-flow login staying?
   If yes, how is `public.users` creation guaranteed for users whose metadata is
   incomplete at Supabase Auth creation time?

4. Should Phase 5 be treated as superseded?
   If dual-write was intentionally skipped, the docs should say so directly.

5. Is vector/hybrid search meant to be externally exposed now, or only retained
   as an internal capability for later clients?

6. What is the operational owner for:
   - Phase 1 migration execution
   - Phase 2 backfills
   - embedding queue draining
   - matview refresh cadence
   - post-migration validation

7. Are bounties supposed to support domain-tag filtering at all?
   The current API suggests yes; the current schema says no.

8. Is atom source artifact upload in scope for the API itself, or should the
   API stop pretending the artifact was stored when only metadata was written?

## What Changed Relative To The Migration Plan

### Things that are now true in the repo

- Supabase auth/token validation is already in the API runtime.
- Supabase query builder / RPCs already replaced asyncpg in API handlers.
- `/catalog/manifest` is effectively gone.
- SQLite manifest generation already uses Supabase as source of truth.
- Phase 6 search/embedding primitives already exist in the database layer.

### Things the plan still says, but the repo no longer reflects

- Phase 5 still describes a future dual-write migration period.
- The plan assumes the migration is still approaching cutover; the repo is
  already written in a cut-over shape.

### Things the plan expects, but the repo only partially satisfies

- RLS-authoritative request access model
- richer registry writes at publish time
- fully exposed Phase 6 search API surface
- operationalized embedding worker / migration workflow

### Things the plan clarified that should now replace the old TECH_GAP assumptions

- Entitlement vs. visibility is the correct access model. The old document's
  coarse "release flag" framing is obsolete.
- SQLite is a local cache generated from Supabase, not a server-side artifact
  downloaded from a legacy endpoint.
- Supabase Auth is the intended identity substrate; self-issued platform JWTs
  are no longer the architectural baseline.
- Materialized `users.effective_tier` and `atoms.is_publishable` are central to
  performance and policy design, not optional future optimizations.

## Recommended Next Steps

1. Fix the client selection bug first.
   Normal request handlers should not silently prefer the service-role client.

2. Rebase `docs/supabase/phase-5-dual-write-and-cutover.md`.
   The current version describes a migration path the repo no longer follows.

3. Decide the canonical rich-atom write path.
   Then either extend `publish_atom()` or explicitly narrow its contract.

4. Resolve auth-path ownership.
   Pick one supported login path, or document the mixed browser/device-flow
   model and its profile-bootstrap guarantees.

5. Close the search/API gap.
   Either expose `fts | vector | hybrid` intentionally, or mark non-FTS modes
   as internal-only for now.

6. Clean the local verification environment.
   A single supported test env should run the focused Supabase suite without
   interpreter/plugin drift.
