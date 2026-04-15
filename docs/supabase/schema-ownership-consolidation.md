# Supabase Schema Ownership Consolidation

This document records the intended ownership boundary for Supabase schema,
population, and consumer code across the Sciona repos.

## Target Ownership

`sciona-infra` is the single authority for:

- `supabase/config.toml`
- `supabase/migrations/*`
- `supabase/seed.sql`
- `supabase/sql/*`
- schema reset, migration, and validation operator flow

Other repos become consumers:

- `sciona-atoms`: provider discovery and Supabase population/backfill tooling
- `sciona-matcher`: manifest export, snapshot readers, catalog/runtime consumers

## Why Infra Owns Schema

The local and deployed app stack depends on DB-side joins, views, and RPCs, not
just raw tables.

Examples already used by API/frontend surfaces:

- `catalog_atoms_served`
- `get_atom_document`
- `search_atoms_hybrid`
- `originator_impact`
- `get_originator_impact`
- `get_atom_benchmarks`
- `architect_leaderboard`

That makes schema ownership an infrastructure concern rather than a matcher-only
or provider-only concern.

## Migration Divergence Being Resolved

Matcher currently vendors only a subset of the migration tree:

- through `20260402000000_enterprise_auth.sql`

Infra currently contains the superset:

- `20260405000000_badges_and_referrals.sql`
- `20260407000000_architect_leaderboard.sql`
- `20260407100000_reputation_system.sql`

Resets from matcher therefore rebuild an incomplete schema relative to the full
app surface. Resets from infra rebuild the superset.

## Staged Consolidation

### Phase 1: Declare Infra As Authority

- all new migrations land only in `sciona-infra`
- other repos stop adding schema changes locally

### Phase 2: Repoint Consumer Runbooks

- matcher and atoms docs should reference infra as the reset/migration root
- local operator flow becomes:
  1. reset/migrate from `sciona-infra`
  2. populate from `sciona-atoms`
  3. consume/export from matcher

### Phase 3: Repoint Matcher Tests And Scripts

Matcher still has local assumptions tied to `repo_root/supabase`, so the
duplicate tree cannot be deleted immediately.

Required follow-up:

- parameterize matcher local Supabase tests to point at infra's project root
- update matcher validation helpers to call infra-owned SQL/migrations
- move CI reset/setup to infra-owned commands

### Phase 4: Retire Matcher Schema Duplication

Only after the test and CI seams are moved:

- remove duplicated matcher migrations, or
- replace them with a minimal compatibility shim that clearly points to infra

## Repository Responsibilities After Cutover

- `sciona-infra`: schema, RPCs, RLS, views, reset/migrate/validate
- `sciona-atoms`: repo-derived data seeding and file-backed backfills
- `sciona-matcher`: manifest/export and runtime consumers

## Immediate Next Steps

1. Keep `sciona-infra` as the only repo receiving new migrations.
2. Add matcher config for an external Supabase project root.
3. Repoint matcher local Supabase tests to `../sciona-infra/supabase`.
4. Update local reset/reseed docs in matcher and atoms to call infra first.
5. Remove matcher's migration ownership only after those seams are green.
