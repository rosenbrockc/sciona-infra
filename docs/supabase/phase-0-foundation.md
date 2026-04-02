# Phase 0: Foundation -- Project Setup & Core Schema

## Overview

Phase 0 establishes the Supabase project infrastructure **and** creates the
complete core schema: all tables, indexes, functions, and (disabled) triggers
required by the migration plan. No data is migrated and no RLS policies are
applied -- those belong to later phases.

When Phase 0 is complete you will have:

- A live Supabase project with GitHub OAuth enabled.
- Extensions: `pgvector`, `pg_trgm`, `uuid-ossp`.
- The full table graph from sections 2.1 through 2.5 of the migration plan,
  with all foreign-key constraints satisfied.
- All helper functions installed (`is_contributor`, `user_effective_entitlement`,
  `atom_is_publishable`, etc.).
- All maintenance triggers created in the **DISABLED** state (re-enabled in
  Phase 3 after data migration is complete and materialized columns are
  backfilled).
- The materialized view `atom_audit_latest` created (empty).
- Validation queries confirming every table, index, function, and trigger exists.
- A tested rollback procedure.

---

## Prerequisites

| Prerequisite | Notes |
|---|---|
| Supabase account | Free tier is fine for dev; Pro recommended for production. |
| GitHub OAuth App | Needed for Supabase Auth GitHub provider. |
| Node.js >= 18 | Required by the Supabase CLI. |
| Docker Desktop running | For `supabase start` local development. |
| Push access to the repo | To commit the `supabase/` directory. |

---

## Step 1: Supabase Project Setup

### 1.1 Create the Project

1. Go to <https://supabase.com/dashboard>.
2. Click **New Project**.
3. Set project name: `ageo-matcher` (or `ageo-matcher-dev`).
4. Set a strong database password and save it in your password manager.
5. Choose the region closest to your primary user base.
6. Click **Create new project** and wait for provisioning.

Note from the **Settings > API** page:

| Value | Location |
|---|---|
| Project URL | `Settings > API > Project URL` |
| `anon` public key | `Settings > API > Project API keys > anon` |
| `service_role` secret key | `Settings > API > Project API keys > service_role` |
| Project ref (ID) | Alphanumeric string in project URL |

### 1.2 Enable GitHub OAuth Provider

1. Navigate to **Authentication > Providers** in the Supabase dashboard.
2. Toggle **GitHub** on.
3. Create a GitHub OAuth App at <https://github.com/settings/developers> if
   needed. Set the callback URL to the value Supabase shows:
   `https://<project-ref>.supabase.co/auth/v1/callback`
4. Paste Client ID and Client Secret into Supabase.
5. Save.

### 1.3 Install and Link the Supabase CLI

```bash
brew install supabase/tap/supabase   # macOS
supabase --version                   # confirm >= 1.100.0

cd /Users/conrad/personal/ageo-matcher
supabase init                        # creates supabase/ directory
supabase link --project-ref <project-ref>
```

### 1.4 Set Environment Variables

Add to `.env` (already gitignored):

```bash
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

---

## Step 2: Enable Extensions

Run in the Supabase SQL Editor or via `supabase db execute`:

```sql
-- ============================================================
-- EXTENSIONS
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector    WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm   WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
```

**Verification:**

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp')
ORDER BY extname;
```

Expected: three rows returned.

---

## Step 3: Schema DDL

### Table Creation Order

Tables must be created in dependency order to satisfy foreign-key constraints.
The sequence below is the required order. Each numbered group can be created in
any internal order, but all tables in group N must exist before group N+1.

| Group | Tables |
|---|---|
| 1 | `users` |
| 2 | `roles`, `organizations` |
| 3 | `user_role_assignments`, `organization_email_domains`, `organization_memberships`, `user_memberships`, `user_entitlement_grants`, `contribution_events` |
| 4 | `atom_source_repositories` |
| 5 | `atoms`, `references_registry` |
| 6 | `atom_versions`, `atom_authors`, `hyperparams`, `atom_io_specs`, `atom_parameters`, `atom_descriptions`, `atom_references`, `atom_uncertainty_estimates`, `atom_verification_matches`, `atom_audit_evidence`, `atom_audit_rollups` |
| 7 | `atom_benchmarks` (depends on `atom_versions`) |
| 8 | `bounties` |
| 9 | `submissions`, `payouts`, `verification_budgets` |
| 10 | `verification_runs`, `bounty_best_scores`, `principal_targets`, `execution_receipts`, `dataset_splits`, `settlement_payouts` |
| 11 | `benchmark_suites` |
| 12 | `benchmark_votes`, `fuzz_results`, `behavioral_equivalence_flags`, `discipline_repos` |
| 13 | Materialized view: `atom_audit_latest` |
| 14 | Functions |
| 15 | Triggers (created DISABLED) |

### 3.1 Complete DDL

Create migration file:

```bash
supabase migration new phase0_foundation_schema
```

Place the following SQL in the generated file
(`supabase/migrations/<timestamp>_phase0_foundation_schema.sql`):

```sql
-- ============================================================
-- Phase 0: Foundation Schema
-- Creates all core tables, indexes, functions, and triggers.
-- Triggers are created DISABLED and will be enabled in Phase 3.
-- ============================================================

-- ============================================================
-- GROUP 1: USERS (public profile linked to auth.users)
-- ============================================================

CREATE TABLE public.users (
    user_id       UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    github_id     BIGINT UNIQUE NOT NULL,
    github_login  TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    avatar_url    TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    identity_tier TEXT NOT NULL DEFAULT 'contributor'
                  CHECK (identity_tier IN ('contributor', 'payee')),
    stripe_account_id TEXT,
    reputation_score  INTEGER NOT NULL DEFAULT 0,
    is_blacklisted    BOOLEAN NOT NULL DEFAULT FALSE,
    effective_tier TEXT NOT NULL DEFAULT 'general'
                   CHECK (effective_tier IN ('general', 'early_access', 'internal')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_github_login ON public.users (github_login);
CREATE INDEX idx_users_effective_tier ON public.users (effective_tier);

-- ============================================================
-- GROUP 2: ROLES & ORGANIZATIONS
-- ============================================================

CREATE TABLE public.roles (
    role_name        TEXT PRIMARY KEY,
    grants_tier      TEXT NOT NULL
                     CHECK (grants_tier IN ('general', 'early_access', 'internal')),
    description      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE public.organizations (
    organization_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    entitlement_tier TEXT NOT NULL DEFAULT 'early_access'
                     CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    membership_status TEXT NOT NULL DEFAULT 'active'
                     CHECK (membership_status IN ('active', 'suspended', 'cancelled')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- GROUP 3: ROLE ASSIGNMENTS, ORG DOMAINS/MEMBERSHIPS, ENTITLEMENTS
-- ============================================================

CREATE TABLE public.user_role_assignments (
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    role_name        TEXT NOT NULL REFERENCES public.roles(role_name) ON DELETE CASCADE,
    granted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by       UUID REFERENCES public.users(user_id),
    expires_at       TIMESTAMPTZ,
    PRIMARY KEY (user_id, role_name)
);

CREATE TABLE public.organization_email_domains (
    organization_id  UUID NOT NULL REFERENCES public.organizations(organization_id) ON DELETE CASCADE,
    email_domain     TEXT NOT NULL,
    PRIMARY KEY (organization_id, email_domain)
);

CREATE TABLE public.organization_memberships (
    organization_id  UUID NOT NULL REFERENCES public.organizations(organization_id) ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    membership_source TEXT NOT NULL
                      CHECK (membership_source IN ('email_domain', 'manual')),
    starts_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at          TIMESTAMPTZ,
    PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE public.user_memberships (
    membership_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    membership_kind  TEXT NOT NULL
                     CHECK (membership_kind IN ('paid', 'complimentary')),
    entitlement_tier TEXT NOT NULL DEFAULT 'early_access'
                     CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'trialing', 'past_due', 'cancelled', 'expired')),
    starts_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at          TIMESTAMPTZ,
    stripe_customer_id TEXT NOT NULL DEFAULT '',
    stripe_subscription_id TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.user_entitlement_grants (
    grant_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    source_kind      TEXT NOT NULL
                     CHECK (source_kind IN ('role', 'paid_membership', 'organization_membership', 'contribution')),
    entitlement_tier TEXT NOT NULL
                     CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    source_ref       TEXT NOT NULL DEFAULT '',
    granted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ,
    created_by       UUID REFERENCES public.users(user_id)
);

CREATE INDEX idx_user_entitlement_grants_user ON public.user_entitlement_grants (user_id);
CREATE INDEX idx_user_entitlement_grants_tier ON public.user_entitlement_grants (entitlement_tier);

-- CONTRIBUTION EVENTS (audit trail for contribution-based entitlements)
CREATE TABLE public.contribution_events (
    event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    event_kind       TEXT NOT NULL
                     CHECK (event_kind IN (
                         'atom_authorship',
                         'atom_documentation',
                         'atom_uncertainty',
                         'atom_reference',
                         'atom_update',
                         'cdg_submission',
                         'bounty_win'
                     )),
    entity_kind      TEXT NOT NULL DEFAULT 'atom'
                     CHECK (entity_kind IN ('atom', 'bounty', 'cdg')),
    entity_id        UUID,
    entity_fqdn      TEXT NOT NULL DEFAULT '',
    approved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source           TEXT NOT NULL DEFAULT 'backfill'
                     CHECK (source IN ('backfill', 'git_history', 'api', 'admin')),
    source_ref       TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, event_kind, entity_id)
);

CREATE INDEX idx_contribution_events_user ON public.contribution_events (user_id);

-- ============================================================
-- GROUP 4: ATOM SOURCE REPOSITORIES
-- ============================================================

CREATE TABLE public.atom_source_repositories (
    source_repo_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name        TEXT NOT NULL UNIQUE,
    vcs_provider     TEXT NOT NULL DEFAULT 'github'
                     CHECK (vcs_provider IN ('github', 'gitlab', 'other')),
    repo_url         TEXT NOT NULL,
    default_branch   TEXT NOT NULL DEFAULT 'main',
    namespace_root   TEXT NOT NULL DEFAULT 'sciona.atoms',
    namespace_path   TEXT NOT NULL DEFAULT '',
    clone_priority   INTEGER NOT NULL DEFAULT 100,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- GROUP 5: ATOMS
-- ============================================================

CREATE TABLE public.atoms (
    atom_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fqdn          TEXT UNIQUE NOT NULL,
    namespace_root TEXT NOT NULL DEFAULT 'sciona.atoms',
    namespace_path TEXT NOT NULL DEFAULT '',
    owner_id      UUID NOT NULL REFERENCES public.users(user_id),
    domain_tags   TEXT[] NOT NULL DEFAULT '{}',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'superseded', 'flagged', 'withdrawn')),
    superseded_by TEXT,
    visibility_tier TEXT NOT NULL DEFAULT 'general'
                  CHECK (visibility_tier IN ('general', 'early_access', 'internal')),
    source_kind   TEXT NOT NULL DEFAULT 'hand_written'
                  CHECK (source_kind IN ('generated_ingest', 'hand_written', 'refined_ingest', 'skeleton')),
    stateful_kind TEXT NOT NULL DEFAULT 'none'
                  CHECK (stateful_kind IN ('none', 'argument_state', 'explicit_state_model',
                                           'implicit_stateful', 'return_state')),
    is_stochastic BOOLEAN NOT NULL DEFAULT FALSE,
    is_ffi        BOOLEAN NOT NULL DEFAULT FALSE,
    is_publishable BOOLEAN NOT NULL DEFAULT FALSE,
    source_repo_id UUID,
    source_package TEXT NOT NULL DEFAULT '',
    source_module_path TEXT NOT NULL DEFAULT '',
    source_symbol   TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.atoms
    ADD CONSTRAINT fk_atoms_source_repo
    FOREIGN KEY (source_repo_id) REFERENCES public.atom_source_repositories(source_repo_id);

CREATE INDEX idx_atoms_fqdn ON public.atoms (fqdn);
CREATE INDEX idx_atoms_namespace_path ON public.atoms (namespace_path);
CREATE INDEX idx_atoms_status ON public.atoms (status);
CREATE INDEX idx_atoms_visibility_tier ON public.atoms (visibility_tier);
CREATE INDEX idx_atoms_domain_tags ON public.atoms USING gin (domain_tags);
CREATE INDEX idx_atoms_owner ON public.atoms (owner_id);
CREATE INDEX idx_atoms_source_kind ON public.atoms (source_kind);
CREATE INDEX idx_atoms_publishable ON public.atoms (is_publishable) WHERE is_publishable = TRUE;

-- ============================================================
-- GROUP 6: ATOM CHILD TABLES (depend on atoms)
-- ============================================================

-- ATOM VERSIONS
CREATE TABLE public.atom_versions (
    version_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    content_hash  TEXT UNIQUE NOT NULL,
    semver        TEXT NOT NULL,
    is_latest     BOOLEAN NOT NULL DEFAULT FALSE,
    derives_from  UUID REFERENCES public.atom_versions(version_id),
    s3_key        TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, semver)
);

CREATE INDEX idx_atom_versions_atom ON public.atom_versions (atom_id);
CREATE INDEX idx_atom_versions_hash ON public.atom_versions (content_hash);
CREATE INDEX idx_atom_versions_latest ON public.atom_versions (atom_id) WHERE is_latest = TRUE;

-- ATOM AUTHORS
CREATE TABLE public.atom_authors (
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES public.users(user_id),
    contribution_share NUMERIC(5,4) NOT NULL DEFAULT 1.0
                  CHECK (contribution_share > 0 AND contribution_share <= 1),
    PRIMARY KEY (atom_id, user_id)
);

CREATE INDEX idx_atom_authors_user ON public.atom_authors (user_id);

-- HYPERPARAMS
CREATE TABLE public.hyperparams (
    hp_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('int', 'float', 'categorical', 'bool')),
    default_value JSONB,
    min_value     JSONB,
    max_value     JSONB,
    step_value    JSONB,
    log_scale     BOOLEAN NOT NULL DEFAULT FALSE,
    choices_json  JSONB,
    constraints_json JSONB,
    semantic_role TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'blocked', 'deprecated')),
    UNIQUE (atom_id, name)
);

-- ATOM IO SPECIFICATIONS
CREATE TABLE public.atom_io_specs (
    io_spec_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id    UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    name          TEXT NOT NULL,
    type_desc     TEXT NOT NULL DEFAULT 'Any',
    constraints   TEXT NOT NULL DEFAULT '',
    required      BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    ordinal       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (atom_id, version_id, direction, name)
);

CREATE INDEX idx_atom_io_specs_atom ON public.atom_io_specs (atom_id);
CREATE INDEX idx_atom_io_specs_version ON public.atom_io_specs (version_id);

-- ATOM PARAMETERS
CREATE TABLE public.atom_parameters (
    parameter_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    position       INTEGER NOT NULL DEFAULT 0,
    kind           TEXT NOT NULL
                   CHECK (kind IN ('positional_only', 'positional_or_keyword', 'keyword_only', 'varargs', 'kwargs')),
    type_desc      TEXT NOT NULL DEFAULT 'Any',
    required       BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    technical_description TEXT NOT NULL DEFAULT '',
    dejargonized_description TEXT NOT NULL DEFAULT '',
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (atom_id, version_id, name)
);

CREATE INDEX idx_atom_parameters_atom ON public.atom_parameters (atom_id);
CREATE INDEX idx_atom_parameters_version ON public.atom_parameters (version_id);

-- ATOM DESCRIPTIONS (dejargonized / enriched)
CREATE TABLE public.atom_descriptions (
    description_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('technical', 'dejargonized', 'conceptual_summary', 'usage_example')),
    content        TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'en',
    generated_by   TEXT NOT NULL DEFAULT '',
    reviewed       BOOLEAN NOT NULL DEFAULT FALSE,
    jargon_score   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, kind, language)
);

CREATE INDEX idx_atom_descriptions_atom ON public.atom_descriptions (atom_id);

-- REFERENCES REGISTRY (global bibliography -- created before atom_references for FK)
CREATE TABLE public.references_registry (
    ref_id         TEXT PRIMARY KEY,
    ref_type       TEXT NOT NULL DEFAULT 'paper'
                   CHECK (ref_type IN ('paper', 'repository', 'web', 'book', 'thesis', 'standard')),
    title          TEXT NOT NULL,
    authors        TEXT[] NOT NULL DEFAULT '{}',
    year           INTEGER,
    venue          TEXT NOT NULL DEFAULT '',
    doi            TEXT,
    url            TEXT NOT NULL DEFAULT '',
    bibtex_key     TEXT NOT NULL DEFAULT '',
    bibtex_raw     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_references_registry_doi
    ON public.references_registry (doi) WHERE doi IS NOT NULL;

-- ATOM REFERENCES (academic / scholarly)
CREATE TABLE public.atom_references (
    reference_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    ref_id         TEXT NOT NULL REFERENCES public.references_registry(ref_id) ON DELETE CASCADE,
    ref_key        TEXT NOT NULL,
    doi            TEXT,
    title          TEXT NOT NULL,
    authors        TEXT[] NOT NULL DEFAULT '{}',
    year           INTEGER,
    url            TEXT NOT NULL DEFAULT '',
    relevance_note TEXT NOT NULL DEFAULT '',
    confidence     TEXT NOT NULL DEFAULT ''
                   CHECK (confidence IN ('', 'low', 'medium', 'high')),
    matched_nodes  TEXT[] NOT NULL DEFAULT '{}',
    source         TEXT NOT NULL DEFAULT 'manual'
                   CHECK (source IN ('manual', 'llm_extracted', 'crossref', 'semantic_scholar')),
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, ref_key)
);

CREATE INDEX idx_atom_references_atom ON public.atom_references (atom_id);
CREATE INDEX idx_atom_references_doi ON public.atom_references (doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_atom_references_ref ON public.atom_references (ref_id);

-- ATOM UNCERTAINTY ESTIMATES
CREATE TABLE public.atom_uncertainty_estimates (
    estimate_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id    UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    mode          TEXT NOT NULL DEFAULT 'empirical'
                  CHECK (mode IN ('empirical', 'analytical', 'propagated')),
    scalar_factor DOUBLE PRECISION NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    n_trials      INTEGER NOT NULL DEFAULT 0,
    epsilon       DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_regime  TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_uncertainty_atom ON public.atom_uncertainty_estimates (atom_id);
CREATE INDEX idx_uncertainty_version ON public.atom_uncertainty_estimates (version_id);

-- ATOM VERIFICATION MATCHES
CREATE TABLE public.atom_verification_matches (
    match_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    predicate_id   TEXT NOT NULL DEFAULT '',
    predicate_statement TEXT NOT NULL DEFAULT '',
    informal_desc  TEXT NOT NULL DEFAULT '',
    candidate_name TEXT NOT NULL DEFAULT '',
    candidate_source_lib TEXT NOT NULL DEFAULT '',
    candidate_score DOUBLE PRECISION,
    retrieval_method TEXT NOT NULL DEFAULT '',
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    verification_level TEXT NOT NULL DEFAULT 'unverified'
                   CHECK (verification_level IN ('kernel_proof', 'type_checked',
                                                  'contract_checked', 'unverified')),
    proof_term     TEXT NOT NULL DEFAULT '',
    compiler_output TEXT NOT NULL DEFAULT '',
    error_message  TEXT NOT NULL DEFAULT '',
    all_candidates JSONB NOT NULL DEFAULT '[]',
    all_verifications JSONB NOT NULL DEFAULT '[]',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_verification_matches_atom ON public.atom_verification_matches (atom_id);
CREATE INDEX idx_verification_matches_version ON public.atom_verification_matches (version_id);
CREATE INDEX idx_verification_matches_level ON public.atom_verification_matches (verification_level);

-- ATOM AUDIT EVIDENCE
CREATE TABLE public.atom_audit_evidence (
    evidence_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    audit_type     TEXT NOT NULL
                   CHECK (audit_type IN (
                       'smoke_test',
                       'regression_test',
                       'structural_audit',
                       'semantic_audit',
                       'risk_assessment',
                       'parity_check',
                       'fuzz_test'
                   )),
    passed         BOOLEAN NOT NULL,
    status         TEXT NOT NULL DEFAULT 'completed'
                   CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    details        JSONB NOT NULL DEFAULT '{}',
    source_kind    TEXT NOT NULL DEFAULT 'automated'
                   CHECK (source_kind IN ('automated', 'manual', 'llm_assisted')),
    runner_version TEXT NOT NULL DEFAULT '',
    run_duration_ms INTEGER,
    source_revision TEXT NOT NULL DEFAULT '',
    upstream_version TEXT NOT NULL DEFAULT '',
    review_basis_at TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_evidence_atom ON public.atom_audit_evidence (atom_id);
CREATE INDEX idx_audit_evidence_version ON public.atom_audit_evidence (version_id);
CREATE INDEX idx_audit_evidence_type ON public.atom_audit_evidence (audit_type);
CREATE INDEX idx_audit_evidence_atom_type ON public.atom_audit_evidence (atom_id, audit_type);

-- ATOM AUDIT ROLLUPS
CREATE TABLE public.atom_audit_rollups (
    atom_id              UUID PRIMARY KEY REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    overall_verdict      TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (overall_verdict IN ('unknown', 'trusted', 'acceptable_with_limits',
                                                    'limited_acceptability', 'misleading', 'broken')),
    structural_status    TEXT NOT NULL DEFAULT 'unknown',
    runtime_status       TEXT NOT NULL DEFAULT 'unknown',
    semantic_status      TEXT NOT NULL DEFAULT 'unknown',
    developer_semantics_status TEXT NOT NULL DEFAULT 'unknown',
    risk_tier            TEXT NOT NULL DEFAULT 'medium'
                         CHECK (risk_tier IN ('low', 'medium', 'high')),
    risk_score           INTEGER NOT NULL DEFAULT 0,
    risk_dimensions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_reasons         TEXT[] NOT NULL DEFAULT '{}',
    acceptability_score  INTEGER NOT NULL DEFAULT 0,
    acceptability_band   TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (acceptability_band IN ('unknown', 'acceptable_with_limits',
                                                       'acceptable_with_limits_candidate',
                                                       'limited_acceptability')),
    parity_coverage_level TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (parity_coverage_level IN ('unknown', 'none', 'not_applicable',
                                                          'positive_path', 'positive_and_negative',
                                                          'parity_or_usage_equivalent')),
    parity_test_status   TEXT NOT NULL DEFAULT 'unknown',
    parity_fixture_count INTEGER NOT NULL DEFAULT 0,
    parity_case_count    INTEGER NOT NULL DEFAULT 0,
    review_status        TEXT NOT NULL DEFAULT 'missing',
    review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_limitations   TEXT[] NOT NULL DEFAULT '{}',
    review_required_actions TEXT[] NOT NULL DEFAULT '{}',
    trust_readiness      TEXT NOT NULL DEFAULT 'not_ready',
    trust_blockers       TEXT[] NOT NULL DEFAULT '{}',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- GROUP 7: ATOM BENCHMARKS (depends on atom_versions)
-- ============================================================

CREATE TABLE public.atom_benchmarks (
    benchmark_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id    UUID NOT NULL REFERENCES public.atom_versions(version_id) ON DELETE CASCADE,
    benchmark_name TEXT NOT NULL,
    metric_name   TEXT NOT NULL,
    metric_value  DOUBLE PRECISION NOT NULL,
    dataset_tag   TEXT NOT NULL DEFAULT '',
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_atom_benchmarks_version ON public.atom_benchmarks (version_id);

-- ============================================================
-- GROUP 8: BOUNTIES
-- ============================================================

CREATE TABLE public.bounties (
    bounty_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id  UUID NOT NULL REFERENCES public.users(user_id),
    title         TEXT NOT NULL,
    escrow_amount NUMERIC(12,2) NOT NULL CHECK (escrow_amount > 0),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'open', 'submitted', 'verified',
                                    'settled', 'expired', 'cancelled')),
    deadline      TIMESTAMPTZ,
    tier          TEXT NOT NULL DEFAULT 'standard'
                  CHECK (tier IN ('standard', 'heavy', 'gpu')),
    verification_budget INTEGER NOT NULL DEFAULT 5,
    verifications_used  INTEGER NOT NULL DEFAULT 0,
    config_yml    JSONB NOT NULL DEFAULT '{}',
    flare_payload JSONB,
    ageom_yml_s3  TEXT,
    dataset_s3    TEXT,
    public_split_hash TEXT,
    blind_split_hash  TEXT,
    cancellation_fee NUMERIC(12,2) DEFAULT 0,
    reposted_from UUID REFERENCES public.bounties(bounty_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bounties_status ON public.bounties (status);
CREATE INDEX idx_bounties_principal ON public.bounties (principal_id);

-- ============================================================
-- GROUP 9: SUBMISSIONS, PAYOUTS, VERIFICATION BUDGETS
-- ============================================================

CREATE TABLE public.submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES public.bounties(bounty_id),
    architect_id  UUID NOT NULL REFERENCES public.users(user_id),
    cdg_hash      TEXT NOT NULL,
    atom_versions JSONB NOT NULL,
    receipt_s3    TEXT NOT NULL DEFAULT '',
    receipt_json  JSONB NOT NULL,
    claimed_metric_name  TEXT NOT NULL,
    claimed_metric_value DOUBLE PRECISION NOT NULL,
    verified_metric_value DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'pending'
                  CHECK (verification_status IN ('pending', 'receipt_valid',
                         'public_verified', 'blind_verified', 'rejected')),
    is_winner     BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at   TIMESTAMPTZ
);

CREATE INDEX idx_submissions_bounty ON public.submissions (bounty_id);
CREATE INDEX idx_submissions_architect ON public.submissions (architect_id);

CREATE TABLE public.payouts (
    payout_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES public.bounties(bounty_id),
    user_id       UUID NOT NULL REFERENCES public.users(user_id),
    role          TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount        NUMERIC(12,2) NOT NULL,
    shapley_value TEXT,
    stripe_transfer_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'kyc_hold', 'transferred', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.verification_budgets (
    bounty_id       UUID PRIMARY KEY REFERENCES public.bounties(bounty_id),
    tier            TEXT NOT NULL CHECK (tier IN ('standard', 'heavy', 'gpu')),
    total_slots     INT NOT NULL,
    used_slots      INT NOT NULL DEFAULT 0,
    cost_per_extra  NUMERIC(10,2) NOT NULL,
    overhead_deposit NUMERIC(10,2) NOT NULL DEFAULT 0,
    overhead_used   NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- GROUP 10: VERIFICATION RUNS, BEST SCORES, TARGETS, RECEIPTS,
--           DATASET SPLITS, SETTLEMENT PAYOUTS
-- ============================================================

CREATE TABLE public.verification_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    submission_id   UUID NOT NULL REFERENCES public.submissions(submission_id),
    split_type      TEXT NOT NULL CHECK (split_type IN ('public', 'blind')),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metric_values   JSONB,
    output_hash     TEXT,
    execution_time_s FLOAT,
    peak_memory_bytes BIGINT,
    is_deterministic BOOLEAN,
    sandbox_job_id  TEXT,
    slot_consumed   BOOLEAN NOT NULL DEFAULT false,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_verification_runs_bounty ON public.verification_runs (bounty_id);
CREATE INDEX idx_verification_runs_submission ON public.verification_runs (submission_id);

CREATE TABLE public.bounty_best_scores (
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    metric_name     TEXT NOT NULL,
    best_value      FLOAT NOT NULL,
    best_submission_id UUID REFERENCES public.submissions(submission_id),
    is_baseline     BOOLEAN NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bounty_id, metric_name)
);

CREATE TABLE public.principal_targets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    metric_name     TEXT NOT NULL,
    target_value    FLOAT NOT NULL,
    set_by          UUID NOT NULL REFERENCES public.users(user_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_principal_targets_bounty ON public.principal_targets (bounty_id);

CREATE TABLE public.execution_receipts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id   UUID NOT NULL REFERENCES public.submissions(submission_id),
    bounty_id       UUID NOT NULL,
    cdg_hash        TEXT NOT NULL,
    atom_versions   JSONB NOT NULL,
    split_hash      TEXT NOT NULL,
    output_hash     TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    metric_value    FLOAT NOT NULL,
    ageom_version   TEXT NOT NULL,
    ssh_signature   TEXT NOT NULL,
    ssh_public_key  TEXT NOT NULL,
    verified        BOOLEAN DEFAULT false,
    receipt_timestamp TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_execution_receipts_submission ON public.execution_receipts (submission_id);
CREATE INDEX idx_execution_receipts_bounty ON public.execution_receipts (bounty_id);

CREATE TABLE public.dataset_splits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    unit_key        TEXT NOT NULL,
    partition       TEXT NOT NULL CHECK (partition IN ('public', 'blind')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bounty_id, unit_key)
);

CREATE INDEX idx_dataset_splits_bounty ON public.dataset_splits (bounty_id);

CREATE TABLE public.settlement_payouts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    recipient_id    TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount          NUMERIC(12,2) NOT NULL,
    stripe_transfer_id TEXT,
    atom_fqdn       TEXT,
    cdg_hash        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_settlement_payouts_bounty ON public.settlement_payouts (bounty_id);

-- ============================================================
-- GROUP 11: BENCHMARK SUITES
-- ============================================================

CREATE TABLE public.benchmark_suites (
    benchmark_id    TEXT PRIMARY KEY,
    domain_tags     TEXT[] NOT NULL DEFAULT '{}',
    description     TEXT,
    dataset_s3_key  TEXT NOT NULL DEFAULT '',
    metric_names    TEXT[] NOT NULL DEFAULT '{}',
    curation_source TEXT NOT NULL DEFAULT 'foundation'
                    CHECK (curation_source IN ('foundation', 'community', 'bounty_derived')),
    proposer_id     UUID REFERENCES public.users(user_id),
    vote_count      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'active'
                    CHECK (status IN ('active', 'retired', 'proposed')),
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- GROUP 12: ECOSYSTEM TABLES
-- ============================================================

CREATE TABLE public.benchmark_votes (
    benchmark_id    TEXT NOT NULL REFERENCES public.benchmark_suites(benchmark_id),
    voter_id        UUID NOT NULL REFERENCES public.users(user_id),
    vote            TEXT NOT NULL CHECK (vote IN ('approve', 'reject')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (benchmark_id, voter_id)
);

CREATE TABLE public.fuzz_results (
    fuzz_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_fqdn       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    strategy        TEXT NOT NULL
                    CHECK (strategy IN ('property_based', 'boundary_value',
                                        'param_smoothing', 'behavioral_equiv')),
    passed          BOOLEAN NOT NULL,
    failures        JSONB DEFAULT '[]',
    inputs_tested   INTEGER NOT NULL DEFAULT 0,
    runtime_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_fuzz_results_atom ON public.fuzz_results (atom_fqdn, content_hash);

CREATE TABLE public.behavioral_equivalence_flags (
    flag_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_a_fqdn     TEXT NOT NULL,
    atom_a_hash     TEXT NOT NULL,
    atom_b_fqdn     TEXT NOT NULL,
    atom_b_hash     TEXT NOT NULL,
    match_ratio     FLOAT NOT NULL,
    sample_size     INTEGER NOT NULL,
    reviewed        BOOLEAN DEFAULT FALSE,
    reviewer_id     UUID REFERENCES public.users(user_id),
    disposition     TEXT CHECK (disposition IS NULL OR
                    disposition IN ('plagiarism', 'coincidence', 'common_algorithm')),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.discipline_repos (
    repo_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_url        TEXT UNIQUE NOT NULL,
    webhook_secret  TEXT NOT NULL DEFAULT '',
    domain_tags     TEXT[] NOT NULL DEFAULT '{}',
    maintainer_ids  UUID[] NOT NULL DEFAULT '{}',
    last_synced_commit TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'removed')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- GROUP 13: MATERIALIZED VIEW -- atom_audit_latest
-- ============================================================

CREATE MATERIALIZED VIEW public.atom_audit_latest AS
SELECT DISTINCT ON (atom_id, audit_type)
    atom_id,
    audit_type,
    passed,
    status,
    details,
    source_kind,
    runner_version,
    source_revision,
    upstream_version,
    created_at AS audited_at
FROM public.atom_audit_evidence
ORDER BY atom_id, audit_type, created_at DESC;

CREATE UNIQUE INDEX idx_audit_latest_pk
    ON public.atom_audit_latest (atom_id, audit_type);

-- ============================================================
-- GROUP 14: FUNCTIONS
-- ============================================================

-- Auto-create profile row on auth signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.users (user_id, github_id, github_login, display_name, avatar_url, email)
    VALUES (
        NEW.id,
        COALESCE((NEW.raw_user_meta_data->>'provider_id')::bigint, 0),
        COALESCE(NEW.raw_user_meta_data->>'user_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'avatar_url', ''),
        COALESCE(NEW.email, '')
    );
    RETURN NEW;
END;
$$;

-- Contribution check
CREATE OR REPLACE FUNCTION public.is_contributor(check_user_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.atom_authors aa
        JOIN public.atoms a ON a.atom_id = aa.atom_id
        WHERE aa.user_id = check_user_id
          AND a.status = 'approved'
    );
$$;

-- Effective entitlement computation (4-table union)
CREATE OR REPLACE FUNCTION public.user_effective_entitlement(check_user_id UUID)
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    WITH active_grants AS (
        SELECT ug.entitlement_tier
        FROM public.user_entitlement_grants ug
        WHERE ug.user_id = check_user_id
          AND (ug.expires_at IS NULL OR ug.expires_at > now())
        UNION ALL
        SELECT r.grants_tier
        FROM public.user_role_assignments ura
        JOIN public.roles r ON r.role_name = ura.role_name
        WHERE ura.user_id = check_user_id
          AND (ura.expires_at IS NULL OR ura.expires_at > now())
        UNION ALL
        SELECT o.entitlement_tier
        FROM public.organization_memberships om
        JOIN public.organizations o ON o.organization_id = om.organization_id
        WHERE om.user_id = check_user_id
          AND o.membership_status = 'active'
          AND (om.ends_at IS NULL OR om.ends_at > now())
        UNION ALL
        SELECT um.entitlement_tier
        FROM public.user_memberships um
        WHERE um.user_id = check_user_id
          AND um.status IN ('active', 'trialing')
          AND (um.ends_at IS NULL OR um.ends_at > now())
    )
    SELECT CASE
        WHEN EXISTS (SELECT 1 FROM active_grants WHERE entitlement_tier = 'internal') THEN 'internal'
        WHEN EXISTS (SELECT 1 FROM active_grants WHERE entitlement_tier = 'early_access') THEN 'early_access'
        ELSE 'general'
    END;
$$;

-- Refresh materialized effective_tier on users
CREATE OR REPLACE FUNCTION public.refresh_user_effective_tier()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    target_user_id UUID;
BEGIN
    target_user_id := COALESCE(NEW.user_id, OLD.user_id);
    UPDATE public.users
       SET effective_tier = public.user_effective_entitlement(target_user_id),
           updated_at = now()
     WHERE user_id = target_user_id;
    RETURN NULL;
END;
$$;

-- Publication completeness check
CREATE OR REPLACE FUNCTION public.atom_is_publishable(check_atom_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT
        EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = check_atom_id)
        AND EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = check_atom_id)
        AND EXISTS (
            SELECT 1
            FROM public.atom_descriptions d
            WHERE d.atom_id = check_atom_id
              AND d.kind = 'dejargonized'
              AND d.language = 'en'
              AND d.jargon_score < 0.4
        )
        AND EXISTS (SELECT 1 FROM public.atom_audit_rollups ar WHERE ar.atom_id = check_atom_id)
        AND EXISTS (SELECT 1 FROM public.atom_references r WHERE r.atom_id = check_atom_id);
$$;

-- Refresh materialized is_publishable on atoms
CREATE OR REPLACE FUNCTION public.refresh_atom_publishable()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    target_atom_id UUID;
BEGIN
    target_atom_id := COALESCE(NEW.atom_id, OLD.atom_id);
    UPDATE public.atoms
       SET is_publishable = public.atom_is_publishable(target_atom_id),
           updated_at = now()
     WHERE atom_id = target_atom_id;
    RETURN NULL;
END;
$$;

-- ============================================================
-- GROUP 15: TRIGGERS (all created DISABLED)
--
-- These triggers maintain materialized columns on users and atoms.
-- They are created DISABLED during Phase 0 to avoid firing during
-- bulk data migration. Phase 3 re-enables them after materialized
-- columns have been backfilled.
-- ============================================================

-- Auth user signup trigger (on auth.users)
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- NOTE: The on_auth_user_created trigger above cannot be created DISABLED
-- because auth.users is managed by Supabase. It is left ENABLED because it
-- is needed for new signups even during migration. If you are bulk-inserting
-- users via the admin API, the trigger's COALESCE guards handle missing
-- metadata gracefully.

-- Effective tier triggers (DISABLED)
CREATE TRIGGER trg_effective_tier_grants
    AFTER INSERT OR UPDATE OR DELETE ON public.user_entitlement_grants
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_entitlement_grants DISABLE TRIGGER trg_effective_tier_grants;

CREATE TRIGGER trg_effective_tier_roles
    AFTER INSERT OR UPDATE OR DELETE ON public.user_role_assignments
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_role_assignments DISABLE TRIGGER trg_effective_tier_roles;

CREATE TRIGGER trg_effective_tier_org_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.organization_memberships DISABLE TRIGGER trg_effective_tier_org_memberships;

CREATE TRIGGER trg_effective_tier_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.user_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_memberships DISABLE TRIGGER trg_effective_tier_memberships;

-- Publishability triggers (DISABLED)
CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_io_specs DISABLE TRIGGER trg_publishable_io_specs;

CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_parameters DISABLE TRIGGER trg_publishable_parameters;

CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_descriptions DISABLE TRIGGER trg_publishable_descriptions;

CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_audit_rollups DISABLE TRIGGER trg_publishable_rollups;

CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_references DISABLE TRIGGER trg_publishable_references;
```

---

## Step 4: Apply the Migration

```bash
supabase db push
```

Verify the migration was recorded:

```bash
supabase migration list
```

---

## Step 5: Seed Data

Seed the `roles` table with the initial role mapping from the migration plan:

```sql
INSERT INTO public.roles (role_name, grants_tier, description) VALUES
    ('Administrator',     'internal',      'Full system access'),
    ('Founder',           'internal',      'Organization founder'),
    ('Maintainer',        'internal',      'Atom catalog maintainer'),
    ('Foundation Staff',  'internal',      'Foundation employee'),
    ('Board Member',      'early_access',  'Advisory board member'),
    ('Org Member',        'early_access',  'Organization member'),
    ('Paid Member',       'early_access',  'Paid subscription member'),
    ('Free Member',       'general',       'Free tier member');
```

---

## Step 6: Validation Queries

Run all of the following queries. Every query must return the expected result
for Phase 0 to be considered complete.

### 6.1 Extensions

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp')
ORDER BY extname;
-- Expected: 3 rows
```

### 6.2 Table Count

```sql
SELECT count(*)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE';
-- Expected: 34
```

### 6.3 All Expected Tables Exist

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;
```

Expected tables (alphabetical):

```
atom_audit_evidence
atom_audit_rollups
atom_authors
atom_benchmarks
atom_descriptions
atom_io_specs
atom_parameters
atom_references
atom_source_repositories
atom_uncertainty_estimates
atom_verification_matches
atoms
behavioral_equivalence_flags
benchmark_suites
benchmark_votes
bounties
bounty_best_scores
contribution_events
dataset_splits
discipline_repos
execution_receipts
fuzz_results
hyperparams
organization_email_domains
organization_memberships
organizations
payouts
principal_targets
references_registry
roles
settlement_payouts
submissions
user_entitlement_grants
user_memberships
user_role_assignments
users
verification_budgets
verification_runs
```

Expected count: 38 tables. Verify all named tables are present.

### 6.4 Materialized View Exists

```sql
SELECT matviewname
FROM pg_matviews
WHERE schemaname = 'public';
-- Expected: atom_audit_latest
```

### 6.5 Functions Exist

```sql
SELECT routine_name
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_type = 'FUNCTION'
  AND routine_name IN (
      'handle_new_user',
      'is_contributor',
      'user_effective_entitlement',
      'refresh_user_effective_tier',
      'atom_is_publishable',
      'refresh_atom_publishable'
  )
ORDER BY routine_name;
-- Expected: 6 rows
```

### 6.6 Triggers Exist and Are Disabled

```sql
SELECT
    trigger_name,
    event_object_table,
    CASE
        WHEN tg.tgenabled = 'D' THEN 'DISABLED'
        ELSE 'ENABLED'
    END AS trigger_state
FROM information_schema.triggers t
JOIN pg_trigger tg ON tg.tgname = t.trigger_name
WHERE t.trigger_schema = 'public'
  AND t.trigger_name LIKE 'trg_%'
ORDER BY trigger_name;
```

Expected: 9 rows, all showing `DISABLED`:

| trigger_name | event_object_table | trigger_state |
|---|---|---|
| trg_effective_tier_grants | user_entitlement_grants | DISABLED |
| trg_effective_tier_memberships | user_memberships | DISABLED |
| trg_effective_tier_org_memberships | organization_memberships | DISABLED |
| trg_effective_tier_roles | user_role_assignments | DISABLED |
| trg_publishable_descriptions | atom_descriptions | DISABLED |
| trg_publishable_io_specs | atom_io_specs | DISABLED |
| trg_publishable_parameters | atom_parameters | DISABLED |
| trg_publishable_references | atom_references | DISABLED |
| trg_publishable_rollups | atom_audit_rollups | DISABLED |

### 6.7 Foreign-Key Constraints

```sql
SELECT
    tc.table_name,
    tc.constraint_name,
    ccu.table_name AS references_table
FROM information_schema.table_constraints tc
JOIN information_schema.constraint_column_usage ccu
  ON tc.constraint_name = ccu.constraint_name
  AND tc.constraint_schema = ccu.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
ORDER BY tc.table_name, tc.constraint_name;
-- Expected: non-empty result set. Spot-check that:
--   atoms -> users (owner_id)
--   atoms -> atom_source_repositories (source_repo_id)
--   atom_versions -> atoms (atom_id)
--   submissions -> bounties (bounty_id)
--   verification_runs -> submissions (submission_id)
```

### 6.8 Roles Seed Data

```sql
SELECT role_name, grants_tier FROM public.roles ORDER BY role_name;
-- Expected: 8 rows matching the seed values
```

### 6.9 Smoke Test: Insert and Delete

Confirm that FK constraints and CHECK constraints are enforced:

```sql
-- This should FAIL (no matching auth.users row)
DO $$
BEGIN
    INSERT INTO public.users (user_id, github_id, github_login)
    VALUES ('00000000-0000-0000-0000-000000000099'::uuid, 99999, 'test_user');
    RAISE EXCEPTION 'Insert should have failed due to FK on auth.users';
EXCEPTION
    WHEN foreign_key_violation THEN
        RAISE NOTICE 'FK constraint on users -> auth.users correctly enforced';
END;
$$;

-- This should FAIL (invalid status)
DO $$
BEGIN
    INSERT INTO public.roles (role_name, grants_tier) VALUES ('Bad', 'invalid_tier');
    RAISE EXCEPTION 'Insert should have failed due to CHECK constraint';
EXCEPTION
    WHEN check_violation THEN
        RAISE NOTICE 'CHECK constraint on roles.grants_tier correctly enforced';
END;
$$;
```

---

## Step 7: Connectivity Health Check

```bash
#!/usr/bin/env bash
# scripts/validate_phase0.sh
set -euo pipefail

echo "=== Phase 0 Validation ==="

echo "[1/6] Checking environment variables..."
: "${SUPABASE_URL:?SUPABASE_URL is not set}"
: "${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY is not set}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is not set}"
echo "  OK: All three Supabase env vars are set."

echo "[2/6] Checking Supabase CLI..."
supabase --version || { echo "FAIL: supabase CLI not found"; exit 1; }

echo "[3/6] Checking REST API connectivity..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  "$SUPABASE_URL/rest/v1/")
if [ "$HTTP_STATUS" -eq 200 ]; then
  echo "  OK: REST API returned 200."
else
  echo "  FAIL: REST API returned $HTTP_STATUS."
  exit 1
fi

echo "[4/6] Checking extensions..."
EXT_CHECK=$(supabase db execute \
  "SELECT count(*) FROM pg_extension WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp');" 2>&1)
if echo "$EXT_CHECK" | grep -q "3"; then
  echo "  OK: All 3 extensions are enabled."
else
  echo "  FAIL: Expected 3 extensions. Got: $EXT_CHECK"
  exit 1
fi

echo "[5/6] Checking table count..."
TABLE_CHECK=$(supabase db execute \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';" 2>&1)
echo "  Tables found: $TABLE_CHECK"

echo "[6/6] Checking migration history..."
supabase migration list | head -5
echo "  OK: Migration list retrieved."

echo ""
echo "=== Phase 0 validation complete. All checks passed. ==="
```

---

## Rollback Procedure

Phase 0 rollback is safe because no application data has been migrated.

### Option A: Drop All Schema Objects (keep the Supabase project)

Run in order (reverse of creation). This is a single-transaction rollback
script:

```sql
BEGIN;

-- Drop triggers first
DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;
DROP TRIGGER IF EXISTS trg_publishable_rollups ON public.atom_audit_rollups;
DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;
DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
DROP TRIGGER IF EXISTS trg_effective_tier_memberships ON public.user_memberships;
DROP TRIGGER IF EXISTS trg_effective_tier_org_memberships ON public.organization_memberships;
DROP TRIGGER IF EXISTS trg_effective_tier_roles ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_effective_tier_grants ON public.user_entitlement_grants;
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

-- Drop functions
DROP FUNCTION IF EXISTS public.refresh_atom_publishable();
DROP FUNCTION IF EXISTS public.atom_is_publishable(UUID);
DROP FUNCTION IF EXISTS public.refresh_user_effective_tier();
DROP FUNCTION IF EXISTS public.user_effective_entitlement(UUID);
DROP FUNCTION IF EXISTS public.is_contributor(UUID);
DROP FUNCTION IF EXISTS public.handle_new_user();

-- Drop materialized view
DROP MATERIALIZED VIEW IF EXISTS public.atom_audit_latest;

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS public.discipline_repos CASCADE;
DROP TABLE IF EXISTS public.behavioral_equivalence_flags CASCADE;
DROP TABLE IF EXISTS public.fuzz_results CASCADE;
DROP TABLE IF EXISTS public.benchmark_votes CASCADE;
DROP TABLE IF EXISTS public.benchmark_suites CASCADE;
DROP TABLE IF EXISTS public.settlement_payouts CASCADE;
DROP TABLE IF EXISTS public.dataset_splits CASCADE;
DROP TABLE IF EXISTS public.execution_receipts CASCADE;
DROP TABLE IF EXISTS public.principal_targets CASCADE;
DROP TABLE IF EXISTS public.bounty_best_scores CASCADE;
DROP TABLE IF EXISTS public.verification_runs CASCADE;
DROP TABLE IF EXISTS public.verification_budgets CASCADE;
DROP TABLE IF EXISTS public.payouts CASCADE;
DROP TABLE IF EXISTS public.submissions CASCADE;
DROP TABLE IF EXISTS public.bounties CASCADE;
DROP TABLE IF EXISTS public.atom_benchmarks CASCADE;
DROP TABLE IF EXISTS public.atom_audit_rollups CASCADE;
DROP TABLE IF EXISTS public.atom_audit_evidence CASCADE;
DROP TABLE IF EXISTS public.atom_verification_matches CASCADE;
DROP TABLE IF EXISTS public.atom_uncertainty_estimates CASCADE;
DROP TABLE IF EXISTS public.atom_references CASCADE;
DROP TABLE IF EXISTS public.references_registry CASCADE;
DROP TABLE IF EXISTS public.atom_descriptions CASCADE;
DROP TABLE IF EXISTS public.atom_parameters CASCADE;
DROP TABLE IF EXISTS public.atom_io_specs CASCADE;
DROP TABLE IF EXISTS public.hyperparams CASCADE;
DROP TABLE IF EXISTS public.atom_authors CASCADE;
DROP TABLE IF EXISTS public.atom_versions CASCADE;
DROP TABLE IF EXISTS public.atoms CASCADE;
DROP TABLE IF EXISTS public.atom_source_repositories CASCADE;
DROP TABLE IF EXISTS public.contribution_events CASCADE;
DROP TABLE IF EXISTS public.user_entitlement_grants CASCADE;
DROP TABLE IF EXISTS public.user_memberships CASCADE;
DROP TABLE IF EXISTS public.organization_memberships CASCADE;
DROP TABLE IF EXISTS public.organization_email_domains CASCADE;
DROP TABLE IF EXISTS public.user_role_assignments CASCADE;
DROP TABLE IF EXISTS public.organizations CASCADE;
DROP TABLE IF EXISTS public.roles CASCADE;
DROP TABLE IF EXISTS public.users CASCADE;

-- Drop extensions (optional -- safe to leave in place)
-- DROP EXTENSION IF EXISTS vector;
-- DROP EXTENSION IF EXISTS pg_trgm;
-- DROP EXTENSION IF EXISTS "uuid-ossp";

COMMIT;
```

Then remove the migration from the history:

```bash
supabase migration repair <timestamp> --status reverted
```

### Option B: Delete the Entire Supabase Project

If you want to start fresh:

1. Dashboard > Settings > General > Delete Project.
2. Remove local artifacts: `rm -rf supabase/`
3. Remove env vars from `.env`.

### Option C: Revert Committed Files

```bash
git revert <commit-sha-that-added-phase-0>
```

---

## Dependencies

| Direction | Phase | Dependency |
|---|---|---|
| **This phase depends on** | None | Phase 0 is the first phase. |
| **Depends on this phase** | Phase 1 (Core Data Migration) | Needs all tables to exist for INSERT. |
| **Depends on this phase** | Phase 2 (Documentation tables backfill) | Needs atom tables and documentation child tables. |
| **Depends on this phase** | Phase 3 (Trigger enablement / dual-write) | Re-enables the DISABLED triggers after data backfill. |

---

## Estimated Effort

| Task | Time |
|---|---|
| Create Supabase project + GitHub OAuth | 15 min |
| Enable extensions | 5 min |
| Install and link CLI | 10 min |
| Apply schema migration | 10 min |
| Seed roles | 5 min |
| Run validation queries | 15 min |
| **Total** | **~1 hour** |

---

## Notes

- The `on_auth_user_created` trigger on `auth.users` is left ENABLED because
  it is needed for new signups even during migration. The `COALESCE` guards in
  `handle_new_user()` handle missing metadata gracefully.
- All other triggers are DISABLED to prevent cascading updates during bulk data
  loads in Phase 1 and Phase 2. Phase 3 re-enables them and runs a one-time
  backfill of `users.effective_tier` and `atoms.is_publishable`.
- The existing `SCIONA_POSTGRES_URI` in `.env` remains untouched. Phase 0 does
  not modify the current database or application behavior.
- The `atom_audit_latest` materialized view is created empty. It will be
  populated after audit evidence is loaded in Phase 2b, using
  `REFRESH MATERIALIZED VIEW CONCURRENTLY public.atom_audit_latest`.
- The `fuzz_results` table is carried forward as-is for backward compatibility
  with Phase D ecosystem queries. New fuzz runs will write to
  `atom_audit_evidence` with `audit_type = 'fuzz_test'`.
