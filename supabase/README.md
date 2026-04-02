# Supabase Migration Workspace

This directory holds the in-repo Supabase configuration, migrations, and
operator SQL used by the phased migration plan under `docs/supabase/`.

Phase order:

1. `migrations/*_phase0_foundation_schema.sql`
2. Phase-specific data backfill scripts under `scripts/`
3. Later migrations for Phase 3 and Phase 6

Manual dashboard steps still required:

- Provision the Supabase project.
- Enable the GitHub auth provider.
- Capture `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`.

Recommended Phase 0 operator flow:

```bash
supabase init
supabase link --project-ref <project-ref>
supabase db push
./scripts/validate_supabase_phase0.sh
```

Rollback helpers live in `supabase/sql/phase0_rollback.sql`.
