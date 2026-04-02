# Supabase Migration Plan

## 1. Overview

### Goals

1. Move the existing PostgreSQL schema into a Supabase-hosted project, gaining managed auth, realtime, and row-level security (RLS) with zero structural regression.
2. Replace the current coarse release flag with an explicit **entitlement vs. visibility** model:
   atoms declare the visibility tier required to access them, while users receive entitlements based on role, membership, and contribution status.
3. Add **contribution-gated access** so that users who contribute approved atoms can receive early access to newly published atoms before general availability.
4. Store **full atom documentation** in the database: inputs/outputs, parameters, dejargonized descriptions, academic references, and audit results. This replaces the ad-hoc `audit_manifest.json` and per-atom `references.json` files with queryable, versioned rows.
5. **Retain the SQLite manifest as a local cache** generated from Supabase rather than raw asyncpg. The `/catalog/manifest` endpoint is removed; `sciona catalog sync` calls a Supabase RPC to populate `~/.sciona/manifest.sqlite`. Pipeline-hot code paths (hyperparams, benchmark priors) continue to read from local SQLite for latency reasons.

### Non-Goals

- Changing the bounty/verification domain logic (carried forward as-is).
- Building a frontend (this plan covers schema + API + data migration only).
- Multi-tenant or organization-level isolation (single-tenant, single Supabase project).

### Constraints

- All existing foreign-key relationships must be preserved.
- The migration must be incremental: dual-write period where both the old PG and Supabase are populated, then cutover.
- Supabase Auth replaces the custom JWT issuer. GitHub OAuth via Supabase Auth's built-in GitHub provider.
- JWT claims should be used only for relatively stable coarse entitlements; rapidly changing contribution state should remain authoritative in Postgres tables.
- Audit rigor must not regress. The database design should preserve both raw audit evidence and final rollups rather than collapsing everything into a single mutable manifest row.

### Design Principles

1. Separate **canonical catalog data** from **derived rollups**.
2. Separate **raw audit evidence** from **published audit summaries**.
3. Separate **atom visibility requirements** from **user entitlements**.
4. Keep the served catalog query simple: RLS should decide whether the user can see the atom, while views expose the normalized documentation bundle.

---

## 2. Schema Design

### 2.1 Auth and Users

Supabase Auth creates `auth.users` automatically. Our `public.users` table becomes a **profile table** linked to `auth.users.id`. The custom JWT machinery in `deps.py` and `routers/auth.py` is replaced by Supabase's `auth.uid()`.

```sql
-- ============================================================
-- AUTH & USERS
-- ============================================================
-- Supabase Auth handles auth.users. This is the public profile.

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
    -- Materialized effective entitlement tier, updated by trigger when
    -- grants/memberships/roles change. Avoids 4-table UNION in RLS hot path.
    effective_tier TEXT NOT NULL DEFAULT 'general'
                   CHECK (effective_tier IN ('general', 'early_access', 'internal')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_github_login ON public.users (github_login);
CREATE INDEX idx_users_effective_tier ON public.users (effective_tier);

-- Auto-create profile row on signup via trigger
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

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

**Device-flow caveat**: The trigger reads `raw_user_meta_data` fields populated by Supabase's built-in GitHub OAuth. When using the CLI device flow, `auth.users` rows created via `supabase.auth.admin.createUser()` may not have this metadata populated. Two options:
1. Use `supabase.auth.signInWithIdToken()` passing the GitHub access token so Supabase populates metadata correctly.
2. Have the trigger tolerate missing metadata (the `COALESCE` calls already handle NULLs) and backfill the profile row in application code after the auth row exists.

Option 1 is preferred; option 2 is the safety net.

### 2.1.1 Entitlements, Roles, and Contribution State

The current plan gates access directly off "is contributor." That is too coarse for the long-term product. The catalog should instead distinguish:

- **visibility**: what an atom requires in order to be visible
- **entitlement**: what access level a user currently has

Recommended model:

```sql
CREATE TABLE public.roles (
    role_name        TEXT PRIMARY KEY,
    grants_tier      TEXT NOT NULL
                     CHECK (grants_tier IN ('general', 'early_access', 'internal')),
    description      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE public.user_role_assignments (
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    role_name        TEXT NOT NULL REFERENCES public.roles(role_name) ON DELETE CASCADE,
    granted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by       UUID REFERENCES public.users(user_id),
    expires_at       TIMESTAMPTZ,
    PRIMARY KEY (user_id, role_name)
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
```

Initial seed rows for `roles` should include:

- `Administrator` -> `internal`
- `Founder` -> `internal`
- `Maintainer` -> `internal`
- `Foundation Staff` -> `internal`
- `Board Member` -> `early_access`
- `Org Member` -> `early_access`
- `Paid Member` -> `early_access`
- `Free Member` -> `general`

Notes:

- Contribution is one source of entitlement, not the only one. The supported sources are:
  - approved atom / documentation / uncertainty / update contributions
  - approved CDGs that use atoms
  - paid membership
  - organization membership via verified email domain or manual assignment
  - roles
- A contribution grant gives `early_access` for **12 months** from approval.
- Paid membership should exist in both places:
  - mirrored Stripe subscription/customer identifiers for operational billing state
  - first-class Postgres membership rows/grants for RLS and auditability
- External contributions do not count until they are represented in the main atom catalog database.
- JWT custom claims may cache coarse fields like `entitlement_tier`, but RLS should remain authoritative from Postgres tables because contribution and membership state changes over time.
- The `users.identity_tier` field from the original plan can stay for payout identity, but it should not be the only input to catalog access.
- Initial role mapping:
  - `internal`: `Administrator`, `Founder`, `Maintainer`, `Foundation Staff`
  - `early_access`: `Board Member`, `Org Member`, `Paid Member`
  - `general`: `Free Member`

### 2.2 Atom Registry

#### Core tables (modified)

```sql
-- ============================================================
-- ATOMS & VERSIONS
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
    -- NEW: required visibility for this atom in the served catalog
    visibility_tier TEXT NOT NULL DEFAULT 'general'
                  CHECK (visibility_tier IN ('general', 'early_access', 'internal')),
    -- Intrinsic atom metadata (not audit findings — these don't change per audit run)
    source_kind   TEXT NOT NULL DEFAULT 'hand_written'
                  CHECK (source_kind IN ('generated_ingest', 'hand_written', 'refined_ingest', 'skeleton')),
    stateful_kind TEXT NOT NULL DEFAULT 'none'
                  CHECK (stateful_kind IN ('none', 'argument_state', 'explicit_state_model',
                                           'implicit_stateful', 'return_state')),
    is_stochastic BOOLEAN NOT NULL DEFAULT FALSE,
    is_ffi        BOOLEAN NOT NULL DEFAULT FALSE,
    -- Materialized publishability flag, updated by trigger on child table changes.
    -- Avoids 5-EXISTS subquery per row in RLS hot path.
    is_publishable BOOLEAN NOT NULL DEFAULT FALSE,
    source_repo_id UUID,
    source_package TEXT NOT NULL DEFAULT '',
    source_module_path TEXT NOT NULL DEFAULT '',
    source_symbol   TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_atoms_fqdn ON public.atoms (fqdn);
CREATE INDEX idx_atoms_namespace_path ON public.atoms (namespace_path);
CREATE INDEX idx_atoms_status ON public.atoms (status);
CREATE INDEX idx_atoms_visibility_tier ON public.atoms (visibility_tier);
CREATE INDEX idx_atoms_domain_tags ON public.atoms USING gin (domain_tags);
CREATE INDEX idx_atoms_owner ON public.atoms (owner_id);
CREATE INDEX idx_atoms_source_kind ON public.atoms (source_kind);
CREATE INDEX idx_atoms_publishable ON public.atoms (is_publishable) WHERE is_publishable = TRUE;

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

ALTER TABLE public.atoms
    ADD CONSTRAINT fk_atoms_source_repo
    FOREIGN KEY (source_repo_id) REFERENCES public.atom_source_repositories(source_repo_id);

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

`visibility_tier` replaces the simpler `release_tier` concept:

- `general`: visible to all authenticated users and, if desired, the public catalog
- `early_access`: visible only to users whose effective entitlement is `early_access` or higher
- `internal`: visible only to internal/staff users or the service role

The atom row also carries namespace and source-discovery fields so agents can discover where code lives before cloning:

- `fqdn`: canonical served identifier
- `namespace_root`: usually `sciona.atoms`
- `namespace_path`: domain-specific namespace such as `fintech` or `bio`
- `source_package`: PEP 420 package path such as `sciona.atoms.fintech`
- `source_module_path`: logical module path inside the source repo
- `source_symbol`: exported symbol name inside the module

**Implementation note**: These namespace/source fields are forward-looking. No current pipeline code in `sciona/sources.py` populates or consumes them — `AtomSource.resolve_source()` uses `(name, package, path, git, ref, cdg_glob)` today. The backfill for the existing 505 atoms should derive these fields from `audit_manifest.json`'s `module_import_path`, `module_path`, and `wrapper_symbol` fields, which map directly. Pipeline integration is Phase 5+ work.

CREATE TABLE public.atom_authors (
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES public.users(user_id),
    contribution_share NUMERIC(5,4) NOT NULL DEFAULT 1.0
                  CHECK (contribution_share > 0 AND contribution_share <= 1),
    PRIMARY KEY (atom_id, user_id)
);

CREATE INDEX idx_atom_authors_user ON public.atom_authors (user_id);

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
```

#### New: IO specifications

Maps directly to the existing `IOSpec` model in `sciona/architect/models.py`.

**Distinction from `atom_parameters`**: `atom_io_specs` describes conceptual data-flow ports from the CDG node decomposition (what data flows in/out at the graph level). `atom_parameters` (below) describes the actual Python callable signature. For leaf atoms these overlap 1:1; for macro atoms with children, io_specs describe the top-level ports while parameters describe the wrapper signature. The backfill should populate both from the same source for leaf atoms and cross-validate them.

```sql
-- ============================================================
-- ATOM IO SPECIFICATIONS
-- ============================================================

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
```

#### New: Published parameter documentation

`hyperparams` is not enough to document normal callable parameters. The served catalog should expose ordinary parameter semantics even when the atom has no tunable optimization hyperparameters.

```sql
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
```

This table is the main answer to the documentation requirement that every published atom have fully documented parameters. `hyperparams` can stay as a separate table for search-space / tuning metadata.

#### New: Dejargonized descriptions

Separate from `atoms.description` (which is the original/technical description). This stores a plain-language, cross-discipline-accessible version.

```sql
-- ============================================================
-- ATOM DESCRIPTIONS (dejargonized / enriched)
-- ============================================================

CREATE TABLE public.atom_descriptions (
    description_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('technical', 'dejargonized', 'conceptual_summary', 'usage_example')),
    content        TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'en',
    generated_by   TEXT NOT NULL DEFAULT '',   -- e.g. 'llm:gpt-4o', 'human', 'ingest'
    reviewed       BOOLEAN NOT NULL DEFAULT FALSE,
    jargon_score   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, kind, language)
);

CREATE INDEX idx_atom_descriptions_atom ON public.atom_descriptions (atom_id);
```

Recommended serving rule:

- every published atom should have at least one `dejargonized` row
- LLM-generated descriptions are acceptable for publication only if `jargon_score < 0.4`
- `conceptual_summary` and `usage_example` remain optional but strongly recommended
- `technical` kind is backfilled from `atoms.description` and `docstring_summary` from the audit manifest; `atoms.description` remains the canonical short technical description for search/display

#### New: Academic references

```sql
-- ============================================================
-- ATOM REFERENCES (academic / scholarly)
-- ============================================================

CREATE TABLE public.atom_references (
    reference_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    -- ref_key is always populated: DOI when available, otherwise a citation key
    -- like "lorimer2005pulsar". This is the dedup key since DOI can be NULL.
    ref_key        TEXT NOT NULL,
    doi            TEXT,
    title          TEXT NOT NULL,
    authors        TEXT[] NOT NULL DEFAULT '{}',
    year           INTEGER,
    url            TEXT NOT NULL DEFAULT '',
    relevance_note TEXT NOT NULL DEFAULT '',
    confidence     TEXT NOT NULL DEFAULT ''
                   CHECK (confidence IN ('', 'low', 'medium', 'high')),
    -- Which CDG node IDs this reference is relevant to (empty = whole atom)
    matched_nodes  TEXT[] NOT NULL DEFAULT '{}',
    source         TEXT NOT NULL DEFAULT 'manual'
                   CHECK (source IN ('manual', 'llm_extracted', 'crossref', 'semantic_scholar')),
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, ref_key)
);

CREATE INDEX idx_atom_references_atom ON public.atom_references (atom_id);
CREATE INDEX idx_atom_references_doi ON public.atom_references (doi) WHERE doi IS NOT NULL;
```

#### New: Uncertainty estimates

Empirical sensitivity characterizations from `uncertainty.json` files. These are quantitative measurements distinct from pass/fail audit results — they describe how sensitive an atom's output is to input perturbations.

```sql
-- ============================================================
-- ATOM UNCERTAINTY ESTIMATES
-- ============================================================

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
    input_regime  TEXT NOT NULL DEFAULT '',    -- e.g. "shape=(1000,), dtype=float64"
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_uncertainty_atom ON public.atom_uncertainty_estimates (atom_id);
CREATE INDEX idx_uncertainty_version ON public.atom_uncertainty_estimates (version_id);
```

#### New: Verification matches

Pipeline-produced match artifacts from `matches.json`: proof terms, compiler output, verification levels, candidate scores, and retrieval methods. These record how the atom was matched against formal specifications during ingestion.

```sql
-- ============================================================
-- ATOM VERIFICATION MATCHES
-- ============================================================

CREATE TABLE public.atom_verification_matches (
    match_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,

    -- The formal predicate this atom was matched against
    predicate_id   TEXT NOT NULL DEFAULT '',
    predicate_statement TEXT NOT NULL DEFAULT '',
    informal_desc  TEXT NOT NULL DEFAULT '',

    -- Best verified match
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

    -- Full candidate/verification history (flexible, may be large)
    all_candidates JSONB NOT NULL DEFAULT '[]',
    all_verifications JSONB NOT NULL DEFAULT '[]',

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_verification_matches_atom ON public.atom_verification_matches (atom_id);
CREATE INDEX idx_verification_matches_version ON public.atom_verification_matches (version_id);
CREATE INDEX idx_verification_matches_level ON public.atom_verification_matches (verification_level);
```

### 2.3 Audit Results

Do not model audit state as a single mutable manifest blob inside Postgres. Split it into:

1. raw evidence rows produced by specific audit tools
2. latest per-tool results
3. final atom-level rollups used for serving and ranking

This preserves rigor while allowing later recomputation.

```sql
-- ============================================================
-- ATOM AUDIT EVIDENCE
-- ============================================================

CREATE TABLE public.atom_audit_evidence (
    evidence_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,

    audit_type     TEXT NOT NULL
                   CHECK (audit_type IN (
                       'smoke_test',        -- basic import + call test
                       'regression_test',   -- output stability across versions
                       'structural_audit',  -- AST/contract/typing checks
                       'semantic_audit',    -- LLM-verified meaning preservation
                       'risk_assessment',   -- security/safety tier assignment
                       'parity_check',      -- cross-implementation equivalence
                       'fuzz_test'          -- property-based / boundary fuzzing
                   )),

    passed         BOOLEAN NOT NULL,
    status         TEXT NOT NULL DEFAULT 'completed'
                   CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),

    -- Metadata (type-dependent, kept in JSONB for flexibility)
    -- smoke_test:       { "import_ok": bool, "call_ok": bool, "error": str|null }
    -- regression_test:  { "baseline_hash": str, "delta": float, "metric": str }
    -- structural_audit: { "contracts_present": bool, "typing_complete": bool, "issues": [...] }
    -- semantic_audit:   { "llm_model": str, "confidence": float, "evidence": str }
    -- risk_assessment:  { "risk_tier": "low"|"medium"|"high", "factors": [...] }
    -- parity_check:     { "reference_impl": str, "match_ratio": float, "sample_size": int }
    -- fuzz_test:        { "strategy": str, "inputs_tested": int, "failures": [...] }
    details        JSONB NOT NULL DEFAULT '{}',

    -- Provenance
    source_kind    TEXT NOT NULL DEFAULT 'automated'
                   CHECK (source_kind IN ('automated', 'manual', 'llm_assisted')),
    runner_version TEXT NOT NULL DEFAULT '',      -- e.g. 'ingest-v2.3.1'
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
    -- Per-dimension risk breakdown (structural, fidelity, evidence_gap, statefulness, generation, ffi, semantics_proxy)
    risk_dimensions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_reasons         TEXT[] NOT NULL DEFAULT '{}',
    acceptability_score  INTEGER NOT NULL DEFAULT 0,
    acceptability_band   TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (acceptability_band IN ('unknown', 'acceptable_with_limits',
                                                       'acceptable_with_limits_candidate',
                                                       'limited_acceptability')),
    -- Parity coverage
    parity_coverage_level TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (parity_coverage_level IN ('unknown', 'none', 'not_applicable',
                                                          'positive_path', 'positive_and_negative',
                                                          'parity_or_usage_equivalent')),
    parity_test_status   TEXT NOT NULL DEFAULT 'unknown',
    parity_fixture_count INTEGER NOT NULL DEFAULT 0,
    parity_case_count    INTEGER NOT NULL DEFAULT 0,
    -- Review state
    review_status        TEXT NOT NULL DEFAULT 'missing',
    review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_limitations   TEXT[] NOT NULL DEFAULT '{}',
    review_required_actions TEXT[] NOT NULL DEFAULT '{}',
    -- Trust readiness
    trust_readiness      TEXT NOT NULL DEFAULT 'not_ready',
    trust_blockers       TEXT[] NOT NULL DEFAULT '{}',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Recommended rule:

- audit workers write `atom_audit_evidence`
- a rollup job computes `atom_audit_rollups`
- served catalog reads `atom_audit_rollups`

That is safer than having multiple workers overwrite the same canonical atom row.

### 2.3.1 Publication Completeness Gate

End users should only see atoms that satisfy all five documentation pillars:

1. inputs/outputs
2. parameters
3. dejargonized description
4. audit results
5. scholarly references

Internal users must still be able to see partially completed atoms.

The check is implemented as a SQL function used by the maintenance trigger, **not** called per-row in RLS. RLS reads the materialized `atoms.is_publishable` boolean instead.

```sql
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

-- Trigger to keep atoms.is_publishable in sync.
-- Fires on INSERT/UPDATE/DELETE to any of the five pillar tables.
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

CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
```

### 2.4 Bounty and Verification (carried forward)

These tables are carried forward without structural changes. Only RLS policies are added.

```sql
-- ============================================================
-- BOUNTIES (unchanged structure, RLS added below)
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

-- Phase C tables: verification_budgets, verification_runs, bounty_best_scores,
-- principal_targets, execution_receipts, dataset_splits, settlement_payouts
-- carried forward with identical structure. DDL omitted for brevity;
-- see migrations/003_phase_c_verification.sql.
```

### 2.5 Ecosystem (carried forward)

```sql
-- ============================================================
-- ECOSYSTEM (unchanged structure, RLS added below)
-- ============================================================

-- benchmark_suites, benchmark_votes, fuzz_results,
-- behavioral_equivalence_flags, discipline_repos
-- carried forward with identical structure. DDL omitted for brevity;
-- see migrations/004_phase_d_ecosystem.sql.

-- Dashboard views (re-created for Supabase compatibility)
CREATE OR REPLACE VIEW public.originator_impact AS
SELECT
    aa.user_id AS originator_id,
    u.github_login,
    COUNT(DISTINCT b.bounty_id) AS bounty_count,
    COALESCE(SUM(b.escrow_amount), 0) AS total_bounty_value,
    COUNT(DISTINCT aa.atom_id) AS atom_count
FROM public.atom_authors aa
JOIN public.users u ON u.user_id = aa.user_id
JOIN public.atoms a ON a.atom_id = aa.atom_id
LEFT JOIN public.submissions s ON s.atom_versions ? a.fqdn AND s.is_winner = true
LEFT JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled'
GROUP BY aa.user_id, u.github_login;

CREATE OR REPLACE VIEW public.compute_preserved AS
SELECT
    b.bounty_id,
    b.escrow_amount,
    COALESCE(jsonb_array_length(s.atom_versions), 0) AS cdg_node_count,
    COALESCE(jsonb_array_length(s.atom_versions), 0) * 2000 * 5 AS estimated_tokens_saved,
    COALESCE(jsonb_array_length(s.atom_versions), 0) * 2000 * 5 * 0.003 AS estimated_cost_saved
FROM public.bounties b
JOIN public.submissions s ON s.bounty_id = b.bounty_id AND s.is_winner = true
WHERE b.status = 'settled';
```

### 2.6 Helper Functions: Contribution and Effective Entitlement

Used by multiple RLS policies. A user "has contributed" if they are an author of at least one approved atom. Effective entitlement is the access tier actually used for catalog visibility.

```sql
-- ============================================================
-- HELPER: is_contributor(user_uuid)
-- ============================================================

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
```

```sql
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
```

If you later adopt JWT custom claims, this function can prefer a claim for coarse tier checks while still falling back to the table as the source of truth.

**Performance note**: `user_effective_entitlement()` unions across 4 tables. Calling it per-row in RLS is expensive. Instead, `users.effective_tier` is a materialized column updated by trigger:

```sql
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

-- Fire on any change to the four entitlement source tables
CREATE TRIGGER trg_effective_tier_grants
    AFTER INSERT OR UPDATE OR DELETE ON public.user_entitlement_grants
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

CREATE TRIGGER trg_effective_tier_roles
    AFTER INSERT OR UPDATE OR DELETE ON public.user_role_assignments
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

CREATE TRIGGER trg_effective_tier_org_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

CREATE TRIGGER trg_effective_tier_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.user_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
```

RLS policies read `users.effective_tier` directly — a single indexed lookup per request, not a 4-table UNION per row.

### 2.7 RLS Policies

All tables have RLS enabled. Policies are grouped by access pattern.

```sql
-- ============================================================
-- ENABLE RLS ON ALL TABLES
-- ============================================================

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_email_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_entitlement_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_source_repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atoms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_authors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hyperparams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_benchmarks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_io_specs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_parameters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_descriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_references ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_audit_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_audit_rollups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_uncertainty_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_verification_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounties ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounty_best_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.principal_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.execution_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dataset_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settlement_payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.benchmark_suites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.benchmark_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fuzz_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.behavioral_equivalence_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discipline_repos ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- USERS
-- ============================================================

-- Anyone can see profiles
CREATE POLICY users_select_public ON public.users
    FOR SELECT USING (true);

-- Users can update only their own profile
CREATE POLICY users_update_own ON public.users
    FOR UPDATE USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY roles_select_authenticated ON public.roles
    FOR SELECT TO authenticated
    USING (true);

CREATE POLICY user_role_assignments_select_own ON public.user_role_assignments
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY user_entitlement_grants_select_own ON public.user_entitlement_grants
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY organization_memberships_select_own ON public.organization_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY user_memberships_select_own ON public.user_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY atom_source_repositories_select_authenticated ON public.atom_source_repositories
    FOR SELECT TO authenticated
    USING (true);

-- Insert handled by trigger (SECURITY DEFINER), not direct insert
-- Service role handles admin mutations

-- ============================================================
-- ATOMS — visibility tier gated by effective entitlement
-- ============================================================

-- Public: anonymous can see fqdn, description, domain_tags of approved general atoms
-- (enforced via PostgREST anon role + this policy)
-- RLS uses materialized columns (atoms.is_publishable, users.effective_tier)
-- instead of calling functions per row. See triggers in 2.3.1 and 2.6.

CREATE POLICY atoms_select_anon ON public.atoms
    FOR SELECT TO anon
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

-- Authenticated: see all general-tier approved atoms
CREATE POLICY atoms_select_authenticated ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

-- Early access atoms are visible to users with effective early_access or internal entitlement
CREATE POLICY atoms_select_early_access ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'early_access'
        AND is_publishable = TRUE
        AND EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier IN ('early_access', 'internal')
        )
    );

-- Internal atoms are visible only to internal users
CREATE POLICY atoms_select_internal ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'internal'
        AND EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

-- Owners see their own atoms regardless of status/tier
CREATE POLICY atoms_select_own ON public.atoms
    FOR SELECT TO authenticated
    USING (owner_id = auth.uid());

-- Insert: authenticated users can create atoms they own
CREATE POLICY atoms_insert_own ON public.atoms
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = auth.uid());

-- Update: only owner can update their atom
CREATE POLICY atoms_update_own ON public.atoms
    FOR UPDATE TO authenticated
    USING (owner_id = auth.uid())
    WITH CHECK (owner_id = auth.uid());

-- ============================================================
-- ATOM_VERSIONS — follows atom visibility
-- ============================================================

CREATE POLICY atom_versions_select ON public.atom_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_versions.atom_id
            -- Relies on atoms RLS: if the user can see the atom, they can see its versions
        )
    );

CREATE POLICY atom_versions_insert ON public.atom_versions
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_versions.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_AUTHORS — follows atom visibility
-- ============================================================

CREATE POLICY atom_authors_select ON public.atom_authors
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_authors.atom_id
        )
    );

CREATE POLICY atom_authors_insert ON public.atom_authors
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_authors.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- HYPERPARAMS — follows atom visibility
-- ============================================================

CREATE POLICY hyperparams_select ON public.hyperparams
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = hyperparams.atom_id
        )
    );

CREATE POLICY hyperparams_insert ON public.hyperparams
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = hyperparams.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_IO_SPECS — follows atom visibility
-- ============================================================

CREATE POLICY atom_io_specs_select ON public.atom_io_specs
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_io_specs.atom_id
        )
    );

CREATE POLICY atom_io_specs_insert ON public.atom_io_specs
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_io_specs.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_DESCRIPTIONS — follows atom visibility
-- ============================================================

CREATE POLICY atom_descriptions_select ON public.atom_descriptions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_descriptions.atom_id
        )
    );

CREATE POLICY atom_descriptions_insert ON public.atom_descriptions
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_descriptions.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_PARAMETERS — follows atom visibility
-- ============================================================

CREATE POLICY atom_parameters_select ON public.atom_parameters
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_parameters.atom_id
        )
    );

CREATE POLICY atom_parameters_insert ON public.atom_parameters
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_parameters.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_REFERENCES — follows atom visibility
-- ============================================================

CREATE POLICY atom_references_select ON public.atom_references
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
        )
    );

CREATE POLICY atom_references_insert ON public.atom_references
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
              AND a.owner_id = auth.uid()
        )
    );

-- ============================================================
-- ATOM_AUDIT_EVIDENCE / ROLLUPS — follows atom visibility for reads,
-- service role only for writes (automated audit pipeline)
-- ============================================================

CREATE POLICY audit_evidence_select ON public.atom_audit_evidence
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_evidence.atom_id
        )
    );

CREATE POLICY audit_rollups_select ON public.atom_audit_rollups
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_rollups.atom_id
        )
    );

-- No insert/update policy for authenticated users.
-- Audit results are written by the service role (backend audit pipeline).
-- If manual audits are needed, add a policy gated on is_contributor.

-- ============================================================
-- ATOM_UNCERTAINTY_ESTIMATES — follows atom visibility
-- ============================================================

CREATE POLICY uncertainty_estimates_select ON public.atom_uncertainty_estimates
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_uncertainty_estimates.atom_id
        )
    );

-- ============================================================
-- ATOM_VERIFICATION_MATCHES — follows atom visibility
-- ============================================================

CREATE POLICY verification_matches_select ON public.atom_verification_matches
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_verification_matches.atom_id
        )
    );

-- ============================================================
-- ATOM_BENCHMARKS — follows atom version visibility
-- ============================================================

CREATE POLICY atom_benchmarks_select ON public.atom_benchmarks
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atom_versions av
            WHERE av.version_id = atom_benchmarks.version_id
        )
    );

-- ============================================================
-- BOUNTIES — visible to all authenticated, writable by principal
-- ============================================================

CREATE POLICY bounties_select ON public.bounties
    FOR SELECT TO authenticated
    USING (true);

CREATE POLICY bounties_insert ON public.bounties
    FOR INSERT TO authenticated
    WITH CHECK (principal_id = auth.uid());

CREATE POLICY bounties_update_own ON public.bounties
    FOR UPDATE TO authenticated
    USING (principal_id = auth.uid())
    WITH CHECK (principal_id = auth.uid());

-- ============================================================
-- SUBMISSIONS — visible to bounty participants, writable by architect
-- ============================================================

CREATE POLICY submissions_select ON public.submissions
    FOR SELECT TO authenticated
    USING (true);

CREATE POLICY submissions_insert ON public.submissions
    FOR INSERT TO authenticated
    WITH CHECK (architect_id = auth.uid());

-- ============================================================
-- PAYOUTS — visible to the recipient only
-- ============================================================

CREATE POLICY payouts_select_own ON public.payouts
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- VERIFICATION TABLES — authenticated read, service role write
-- ============================================================

CREATE POLICY verification_budgets_select ON public.verification_budgets
    FOR SELECT TO authenticated USING (true);

CREATE POLICY verification_runs_select ON public.verification_runs
    FOR SELECT TO authenticated USING (true);

CREATE POLICY bounty_best_scores_select ON public.bounty_best_scores
    FOR SELECT TO authenticated USING (true);

CREATE POLICY principal_targets_select ON public.principal_targets
    FOR SELECT TO authenticated USING (true);

CREATE POLICY principal_targets_insert ON public.principal_targets
    FOR INSERT TO authenticated
    WITH CHECK (set_by = auth.uid());

CREATE POLICY execution_receipts_select ON public.execution_receipts
    FOR SELECT TO authenticated USING (true);

CREATE POLICY dataset_splits_select ON public.dataset_splits
    FOR SELECT TO authenticated USING (true);

CREATE POLICY settlement_payouts_select ON public.settlement_payouts
    FOR SELECT TO authenticated
    USING (recipient_id = auth.uid()::text);

-- ============================================================
-- ECOSYSTEM TABLES — public read, gated write
-- ============================================================

CREATE POLICY benchmark_suites_select ON public.benchmark_suites
    FOR SELECT USING (true);

CREATE POLICY benchmark_votes_select ON public.benchmark_votes
    FOR SELECT TO authenticated USING (true);

CREATE POLICY benchmark_votes_insert ON public.benchmark_votes
    FOR INSERT TO authenticated
    WITH CHECK (voter_id = auth.uid());

CREATE POLICY fuzz_results_select ON public.fuzz_results
    FOR SELECT USING (true);

CREATE POLICY behavioral_equiv_select ON public.behavioral_equivalence_flags
    FOR SELECT TO authenticated USING (true);

CREATE POLICY discipline_repos_select ON public.discipline_repos
    FOR SELECT USING (true);
```

### 2.8 PostgREST Catalog Views

For unauthenticated catalog browsing, expose a restricted view via the `anon` role.

```sql
-- Minimal public catalog: fqdn, description, domain_tags only
CREATE OR REPLACE VIEW public.catalog_public AS
SELECT fqdn, description, domain_tags
FROM public.atoms
WHERE status = 'approved'
  AND visibility_tier = 'general'
  AND is_publishable = TRUE;

GRANT SELECT ON public.catalog_public TO anon;
```

For authenticated product use, add a richer served view that joins the normalized documentation bundle:

```sql
CREATE OR REPLACE VIEW public.catalog_atoms_served AS
SELECT
    a.atom_id,
    a.fqdn,
    a.domain_tags,
    a.description AS technical_description,
    d.content AS dejargonized_description,
    ar.overall_verdict,
    ar.risk_tier,
    ar.risk_score,
    ar.acceptability_score,
    ar.structural_status,
    ar.runtime_status,
    ar.semantic_status,
    ar.review_status
FROM public.atoms a
LEFT JOIN public.atom_descriptions d
  ON d.atom_id = a.atom_id
 AND d.kind = 'dejargonized'
 AND d.language = 'en'
LEFT JOIN public.atom_audit_rollups ar
  ON ar.atom_id = a.atom_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE;
```

The application can then fetch child rows from:

- `atom_io_specs`
- `atom_parameters`
- `atom_references`
- `atom_audit_latest`

instead of overloading a single manifest response.

### 2.8.1 Search and Full-Document RPC

There are two distinct read paths:

1. **global catalog search**
   This should use as few joins as possible and support agent search efficiently.
2. **atom detail document**
   This should be assembled server-side and loaded on demand.

Recommendation: support **hybrid search**.

- Full-text search is the primary path for exact names, symbols, domains, and keyword lookup.
- Vector search is the semantic fallback for natural-language and cross-discipline discovery.
- The catalog index should support both, with a hybrid ranking mode for agent queries.

Recommended additional serving layer:

```sql
CREATE MATERIALIZED VIEW public.catalog_atoms_index AS
SELECT
    a.atom_id,
    a.fqdn,
    a.namespace_root,
    a.namespace_path,
    a.source_package,
    a.source_module_path,
    a.source_symbol,
    a.source_kind,
    a.stateful_kind,
    a.is_stochastic,
    a.is_ffi,
    sr.repo_name,
    sr.repo_url,
    sr.default_branch,
    a.domain_tags,
    a.visibility_tier,
    a.description AS technical_description,
    d.content AS dejargonized_description,
    d.jargon_score,
    ar.overall_verdict,
    ar.risk_tier,
    ar.risk_score,
    ar.acceptability_score,
    ar.acceptability_band,
    ar.parity_coverage_level,
    ar.trust_readiness,
    ar.review_status,
    COALESCE(ref_counts.reference_count, 0) AS reference_count,
    -- Full-text search document: weighted concat of fqdn, descriptions, domain tags
    setweight(to_tsvector('english', a.fqdn), 'A') ||
    setweight(to_tsvector('english', COALESCE(a.description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(d.content, '')), 'C') ||
    setweight(to_tsvector('english', array_to_string(a.domain_tags, ' ')), 'B')
        AS search_document,
    -- Placeholder for embedding vector; populated by a separate backfill job
    NULL::vector(1536) AS embedding
FROM public.atoms a
LEFT JOIN public.atom_descriptions d
  ON d.atom_id = a.atom_id
 AND d.kind = 'dejargonized'
 AND d.language = 'en'
LEFT JOIN public.atom_audit_rollups ar
  ON ar.atom_id = a.atom_id
LEFT JOIN public.atom_source_repositories sr
  ON sr.source_repo_id = a.source_repo_id
LEFT JOIN (
    SELECT atom_id, COUNT(*) AS reference_count
    FROM public.atom_references
    GROUP BY atom_id
) ref_counts
  ON ref_counts.atom_id = a.atom_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE;

CREATE UNIQUE INDEX idx_catalog_atoms_index_pk
    ON public.catalog_atoms_index (atom_id);

CREATE INDEX idx_catalog_atoms_index_domain_tags
    ON public.catalog_atoms_index USING gin (domain_tags);

CREATE INDEX idx_catalog_atoms_index_fts
    ON public.catalog_atoms_index USING gin (search_document);

CREATE INDEX idx_catalog_atoms_index_embedding
    ON public.catalog_atoms_index USING hnsw (embedding vector_cosine_ops);
```

**Note**: PostgreSQL does not support `ALTER MATERIALIZED VIEW ADD COLUMN`. If the schema of this view changes, it must be dropped and recreated. The `search_document` and `embedding` columns are included in the initial definition to avoid that.

And one server-side RPC for full details:

```sql
-- sketch only
CREATE OR REPLACE FUNCTION public.get_atom_document(request_fqdn TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'atom', row_to_json(a),
        'source_repository', (
            SELECT row_to_json(sr)
            FROM public.atom_source_repositories sr
            WHERE sr.source_repo_id = a.source_repo_id
        ),
        'descriptions', (
            SELECT jsonb_agg(d ORDER BY d.kind, d.language)
            FROM public.atom_descriptions d
            WHERE d.atom_id = a.atom_id
        ),
        'io_specs', (
            SELECT jsonb_agg(ios ORDER BY ios.direction, ios.ordinal)
            FROM public.atom_io_specs ios
            WHERE ios.atom_id = a.atom_id
        ),
        'parameters', (
            SELECT jsonb_agg(p ORDER BY p.position)
            FROM public.atom_parameters p
            WHERE p.atom_id = a.atom_id
        ),
        'references', (
            SELECT jsonb_agg(r ORDER BY r.year NULLS LAST, r.title)
            FROM public.atom_references r
            WHERE r.atom_id = a.atom_id
        ),
        'audit_rollup', (
            SELECT row_to_json(ar)
            FROM public.atom_audit_rollups ar
            WHERE ar.atom_id = a.atom_id
        ),
        'audit_latest', (
            SELECT jsonb_agg(al ORDER BY al.audit_type)
            FROM public.atom_audit_latest al
            WHERE al.atom_id = a.atom_id
        ),
        'uncertainty_estimates', (
            SELECT jsonb_agg(ue ORDER BY ue.created_at DESC)
            FROM public.atom_uncertainty_estimates ue
            WHERE ue.atom_id = a.atom_id
        ),
        'verification_matches', (
            SELECT jsonb_agg(vm ORDER BY vm.verification_level, vm.candidate_score DESC)
            FROM public.atom_verification_matches vm
            WHERE vm.atom_id = a.atom_id
        )
    )
    FROM public.atoms a
    WHERE a.fqdn = request_fqdn;
$$;
```

The source-discovery fields are intentional. Agents should be able to:

1. search the global catalog without cloning every repo
2. identify the backing repo/package/module for a promising atom
3. lazily clone only the relevant namespace repository, such as:
   - `sciona.atoms.fintech`
   - `sciona.atoms.bio`
   - `sciona.atoms.physics`

### 2.9 Product Decisions Incorporated

The following product decisions are now assumed by this plan:

1. Early access comes from any of:
   - approved contribution to an atom
   - approved documentation / uncertainty / update contribution
   - approved new CDG that uses atoms
   - paid membership
   - organization membership
   - role assignment

2. Contribution-based early access lasts **12 months** from approval.

3. Organizations grant access to all matched users, with membership inferred from email domain or manual assignment.

4. All atoms live in the same Supabase project. RLS is the only visibility filter.

5. Migration seeds scholarly references from existing `references.json`. After cutover, references are inserted as part of normal atom creation/update flows.

6. LLM-generated dejargonized descriptions are acceptable if `jargon_score < 0.4`.

7. Users who can see an atom can see all evidence associated with that atom.

8. End users see only atoms that satisfy all five documentation pillars. Internal users may see partially completed atoms.

9. External contributions count only after they are represented in the main atoms database.

10. The default UI loads a high-level tabular object first; full atom details are loaded on demand via server-side RPC.
11. Agent-facing catalog search should support hybrid retrieval: full-text + vector search.
12. The FQN and catalog index must resolve back to source repository, namespace package, module path, and exported symbol so agents can lazily clone only relevant atom repositories.

### 2.10 Remaining Clarifications

Only a small number of implementation details remain open:

1. Which embedding model and dimensionality should power semantic atom search?
2. Should hybrid ranking be a simple weighted merge of full-text + vector scores, or a reranked two-stage retrieval pipeline?
3. How should namespace repositories be registered operationally: manual admin rows in `atom_source_repositories`, repo webhooks, or both?

---

## 3. Migration Strategy

### Phase 1: Provision and Schema (Week 1)

1. Create a Supabase project. Enable GitHub OAuth provider in Supabase Auth settings.
2. Apply the full schema DDL above via the Supabase SQL editor or `supabase db push`.
3. Run the Phase C and Phase D migration DDL (carried forward tables).
4. Enable RLS on all tables and apply all policies.
5. Validate: run the existing test suite against Supabase using the service role key to confirm schema compatibility.

### Phase 2: Data Migration (Week 2)

1. **Users**: Export from old PG. For each user, create a corresponding `auth.users` entry via Supabase Admin API (`supabase.auth.admin.createUser`), mapping `github_id` to the GitHub provider identity. Then insert the `public.users` profile row (or let the trigger handle it).
2. **Atoms, versions, authors, hyperparams, benchmarks**: Direct `pg_dump` / `psql` INSERT export from old PG into Supabase. UUIDs are preserved.
3. **Roles, organizations, memberships, and entitlement grants**: Backfill from existing membership/billing/admin systems before enabling end-user RLS for early access.
4. **Source repository registry**: Seed `atom_source_repositories` for the current repo and any future namespace repos so FQNs can be resolved to code locations.
5. **Bounties and all downstream tables**: Same direct export.
6. **New documentation/audit tables** (backfill from files): see Section 5.

### Phase 3: Dual-Write (Week 3)

1. Modify the API to write to both old PG and Supabase for all mutating operations.
2. Read path still uses old PG.
3. Run consistency checks: compare row counts and checksums nightly.

### Phase 4: Read Cutover (Week 4)

1. Switch read path to Supabase.
2. Keep old PG as read-only fallback.
3. Monitor error rates and latency.

### Phase 5: Full Cutover (Week 5)

1. Remove dual-write. Supabase is the sole database.
2. Remove old PG connection config.
3. Rewrite `snapshot.py` to generate SQLite from Supabase. Remove the `/catalog/manifest` HTTP endpoint.
4. Remove `SCIONA_JWT_PUBLIC_KEY` / `SCIONA_JWT_PRIVATE_KEY` env vars.
5. Verify that `sciona catalog sync`, `load_hyperparams_manifest_sqlite()`, and `load_benchmarks_sqlite()` work against the new Supabase-backed SQLite cache.

---

## 4. Client Migration

### 4.1 Auth (`sciona/api/deps.py` and `sciona/api/routers/auth.py`)

**Before**: Custom JWT issuer with RS256 keys, GitHub device flow implemented manually, `require_auth` decodes JWT and queries `users` table.

**After**:
- Remove `_get_jwt_public_key()`, the manual device flow endpoints, and `_upsert_user_and_issue_jwt`.
- Replace `require_auth` with Supabase JWT validation:

```python
# deps.py — new version (sketch)
from fastapi import Depends, HTTPException, Request
from gotrue import User as GoTrueUser
from supabase import AsyncClient as SupabaseClient

async def get_supabase(request: Request) -> SupabaseClient:
    client = getattr(request.app.state, "supabase", None)
    if client is None:
        raise HTTPException(503, "Supabase not available")
    return client

async def require_auth(
    request: Request,
    supabase: SupabaseClient = Depends(get_supabase),
) -> GoTrueUser:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        raise HTTPException(401, "Missing token")
    user_response = await supabase.auth.get_user(token)
    if not user_response.user:
        raise HTTPException(401, "Invalid token")
    # Check blacklist via profile
    profile = await supabase.table("users").select("is_blacklisted").eq(
        "user_id", user_response.user.id
    ).single().execute()
    if profile.data and profile.data.get("is_blacklisted"):
        raise HTTPException(403, "Account suspended")
    return user_response.user
```

- Replace `routers/auth.py` GitHub device flow with a redirect to Supabase Auth's built-in GitHub OAuth flow, or keep the device flow but use `supabase.auth.sign_in_with_oauth` on the server side.

### 4.2 Database Access (`deps.py` `get_db`)

**Before**: `asyncpg` connection pool on `request.app.state.db_pool`.

**After**: Supabase Python client on `request.app.state.supabase`. For queries that need raw SQL (e.g., complex joins in `catalog.py`), use `supabase.rpc()` with server-side functions or the PostgREST query builder.

Routers that currently build raw SQL (`catalog.py` search) should migrate to PostgREST filters or Postgres functions exposed via `supabase.rpc()`.

### 4.3 Catalog (`sciona/api/routers/catalog.py`)

**Before**: Raw `asyncpg` query + SQLite manifest download endpoint.

**After**:
- `/catalog/search` should read primarily from `catalog_atoms_index` to minimize joins and should support `mode=fts|vector|hybrid`.
- `/catalog/manifest` endpoint is **removed**. The SQLite manifest is generated client-side (see 4.6).
- `/catalog/atom/{fqdn}` (or equivalent) should call `get_atom_document(...)` so the server assembles the full document bundle.
- `snapshot.py` is **rewritten** to generate the SQLite manifest from Supabase rather than raw asyncpg (see 4.6).

### 4.4 Other Routers

- `routers/registry.py`: Replace `asyncpg` calls with Supabase client calls. Atom creation now also inserts IO specs, descriptions, and references.
- `routers/bounty.py`, `routers/verification.py`, `routers/dashboard.py`: Replace `asyncpg` pool with Supabase client. No structural changes to the business logic.

### 4.5 Dependencies

**Add**: `supabase` (Python SDK), `gotrue` (included with supabase SDK).

**Remove** (after full cutover): `asyncpg`, `PyJWT` (Supabase SDK handles JWT validation).

### 4.6 SQLite Manifest as Local Cache

**Motivation**: The CLI (`sciona catalog sync`) downloads `manifest.sqlite` for offline use. Pipeline-hot code paths — `load_hyperparams_manifest_sqlite()` in `sciona/architect/hyperparams.py` and `load_benchmarks_sqlite()` in `sciona/principal/benchmark_priors.py` — read from local SQLite during execution where network latency is unacceptable. The webhook sync in `sciona/ecosystem/webhook_sync.py` also parses manifest SQLite for diffing.

**After**: The SQLite manifest remains as a **local cache**, but its source of truth moves from raw asyncpg to Supabase:

- `snapshot.py` is rewritten: `generate_manifest_sqlite()` calls a Supabase RPC (or PostgREST query) to fetch atoms, hyperparams, and benchmarks, then writes the same SQLite schema locally.
- `sciona catalog sync` calls the rewritten `snapshot.py` instead of downloading a pre-built file from `/catalog/manifest`.
- `load_hyperparams_manifest_sqlite()` and `load_benchmarks_sqlite()` continue to read from `~/.sciona/manifest.sqlite` unchanged.
- `parse_manifest_sqlite()` in webhook sync continues to read from discipline repo SQLite files unchanged.

The manifest is a cache, not the source of truth. Staleness is acceptable for pipeline runs; `sciona catalog sync` refreshes it.

---

## 5. Data Migration: Backfill from Files

### 5.1 Audit Manifest (`data/audit_manifest.json`)

The current audit manifest should backfill two targets:

- raw evidence rows in `atom_audit_evidence`
- final published rollups in `atom_audit_rollups`

Backfill script sketch:

```python
# scripts/backfill_audit_manifest.py (sketch)
import json
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

with open("data/audit_manifest.json") as f:
    manifest = json.load(f)

for atom_entry in manifest["atoms"]:
    fqdn = atom_entry["atom_name"]  # adapt to real fqdn lookup during implementation

    atom = supabase.table("atoms").select("atom_id").eq("fqdn", fqdn).single().execute()
    if not atom.data:
        continue
    atom_id = atom.data["atom_id"]

    # Backfill latest rollup
    supabase.table("atom_audit_rollups").upsert({
        "atom_id": atom_id,
        "overall_verdict": atom_entry.get("overall_verdict", "unknown"),
        "structural_status": atom_entry.get("structural_status", "unknown"),
        "runtime_status": atom_entry.get("runtime_status", "unknown"),
        "semantic_status": atom_entry.get("semantic_status", "unknown"),
        "developer_semantics_status": atom_entry.get("developer_semantics_status", "unknown"),
        "risk_tier": atom_entry.get("risk_tier", "medium"),
        "risk_score": atom_entry.get("risk_score", 0),
        "risk_dimensions": atom_entry.get("risk_dimensions", {}),
        "risk_reasons": atom_entry.get("risk_reasons", []),
        "acceptability_score": atom_entry.get("acceptability_score", 0),
        "acceptability_band": atom_entry.get("acceptability_band", "unknown"),
        "parity_coverage_level": atom_entry.get("parity_coverage_level", "unknown"),
        "parity_test_status": atom_entry.get("parity_test_status", "unknown"),
        "parity_fixture_count": atom_entry.get("parity_fixture_count", 0),
        "parity_case_count": atom_entry.get("parity_case_count", 0),
        "review_status": atom_entry.get("review_status", "missing"),
        "review_semantic_verdict": atom_entry.get("review_semantic_verdict", "unknown"),
        "review_developer_semantics_verdict": atom_entry.get("review_developer_semantics_verdict", "unknown"),
        "review_limitations": atom_entry.get("review_limitations", []),
        "review_required_actions": atom_entry.get("review_required_actions", []),
        "trust_readiness": atom_entry.get("trust_readiness", "not_ready"),
        "trust_blockers": atom_entry.get("trust_blockers", []),
    }).execute()

    # Backfill raw evidence only for durable result families that exist in the manifest
    for audit_type, field_name in [
        ("risk_assessment", "risk_tier"),
        ("structural_audit", "structural_status"),
        ("parity_check", "parity_coverage_level"),
        ("semantic_audit", "semantic_status"),
    ]:
        if atom_entry.get(field_name) is None:
            continue
        supabase.table("atom_audit_evidence").insert({
            "atom_id": atom_id,
            "audit_type": audit_type,
            "passed": atom_entry.get(field_name) in ("pass", "low", "positive_and_negative", "parity_or_usage_equivalent"),
            "details": {
                "field": field_name,
                "value": atom_entry.get(field_name),
                "findings": atom_entry.get("semantic_findings", []) if audit_type == "semantic_audit" else [],
            },
            "source_kind": "automated",
            "runner_version": "backfill-v1",
            "source_revision": atom_entry.get("source_revision", ""),
            "upstream_version": atom_entry.get("upstream_version", ""),
        }).execute()
```

### 5.2 References (`references.json` per atom)

Each atom family may have a `references.json` file in its data directory.

```python
# scripts/backfill_references.py (sketch)
from pathlib import Path

for refs_path in Path("data").rglob("references.json"):
    with open(refs_path) as f:
        refs = json.load(f)

    # Derive atom fqdn from directory structure
    family = refs_path.parent.name
    for ref in refs:
        atom_fqdn = ref.get("atom_fqdn") or f"{family}.{ref.get('atom_name', '')}"
        atom = supabase.table("atoms").select("atom_id").eq("fqdn", atom_fqdn).single().execute()
        if not atom.data:
            continue

        # ref_key is DOI if available, otherwise the citation key (ref_id)
        ref_key = ref.get("doi") or ref.get("ref_id", ref.get("title", "")[:80])
        match_meta = ref.get("match_metadata", {})
        supabase.table("atom_references").insert({
            "atom_id": atom.data["atom_id"],
            "ref_key": ref_key,
            "doi": ref.get("doi"),
            "title": ref["title"],
            "authors": ref.get("authors", []),
            "year": ref.get("year"),
            "url": ref.get("url", ""),
            "relevance_note": match_meta.get("notes", ""),
            "confidence": match_meta.get("confidence", ""),
            "matched_nodes": match_meta.get("matched_nodes", []),
            "source": match_meta.get("match_type", "llm_extracted"),
            "verified": False,
        }).execute()
```

### 5.3 Dejargonized Descriptions

Backfill dejargonized descriptions from the existing description-generation pipeline, but only publish rows whose stored or computed `jargon_score` is below the acceptance threshold.

```python
# scripts/backfill_dejargonized_descriptions.py (sketch)
for atom in atoms:
    generated = build_or_load_dejargonized_description(atom)
    jargon_score = generated["jargon_score"]

    supabase.table("atom_descriptions").upsert({
        "atom_id": atom["atom_id"],
        "kind": "dejargonized",
        "language": "en",
        "content": generated["content"],
        "generated_by": generated["generated_by"],
        "reviewed": generated.get("reviewed", False),
        "jargon_score": jargon_score,
    }).execute()
```

### 5.4 Source Repository Registry and FQN Resolution

Seed the source repository registry before agent-facing search goes live.

```python
# scripts/backfill_source_repositories.py (sketch)
repos = [
    {
        "repo_name": "sciona-atoms-core",
        "repo_url": "https://github.com/.../sciona-atoms-core",
        "namespace_root": "sciona.atoms",
        "namespace_path": "",
        "default_branch": "main",
    },
    {
        "repo_name": "sciona-atoms-fintech",
        "repo_url": "https://github.com/.../sciona-atoms-fintech",
        "namespace_root": "sciona.atoms",
        "namespace_path": "fintech",
        "default_branch": "main",
    },
]

for repo in repos:
    supabase.table("atom_source_repositories").upsert(repo).execute()
```

Each atom backfill should also populate:

- `fqdn`
- `namespace_root`
- `namespace_path`
- `source_repo_id`
- `source_package`
- `source_module_path`
- `source_symbol`

That gives agents enough information to discover the right repo and clone lazily only after identifying a useful atom.

### 5.5 IO Specs (from CDG files and emitter output)

IO specs are already computed during ingestion (`_canonical_node_iospecs` in `emitter.py`) and recorded in CDG JSON files. The backfill reads from `cdg.json` per atom (which contains `inputs` and `outputs` arrays on each node).

```python
# scripts/backfill_io_specs.py (sketch)
# For each atom, read cdg.json to extract IOSpec from the CDG node's
# inputs/outputs arrays. For leaf atoms, also cross-validate against
# the function signature in atoms.py.
for cdg_path in Path("ageoa").rglob("cdg.json"):
    cdg = json.loads(cdg_path.read_text())
    for node in cdg.get("nodes", []):
        if node.get("status") != "atomic":
            continue
        atom_fqdn = derive_fqdn(cdg_path, node)
        atom = lookup_atom(atom_fqdn)
        if not atom:
            continue
        for i, spec in enumerate(node.get("inputs", [])):
            supabase.table("atom_io_specs").insert({
                "atom_id": atom["atom_id"],
                "direction": "input",
                "name": spec["name"],
                "type_desc": spec.get("type_desc", "Any"),
                "constraints": spec.get("constraints", ""),
                "required": spec.get("required", True),
                "default_value_repr": spec.get("default_value_repr", ""),
                "ordinal": i,
            }).execute()
        for i, spec in enumerate(node.get("outputs", [])):
            supabase.table("atom_io_specs").insert({
                "atom_id": atom["atom_id"],
                "direction": "output",
                "name": spec["name"],
                "type_desc": spec.get("type_desc", "Any"),
                "constraints": spec.get("constraints", ""),
                "required": True,
                "default_value_repr": "",
                "ordinal": i,
            }).execute()
```

### 5.6 Parameters (from audit manifest + ghost registry)

There is no existing `parameters` model in the emitter — callable parameter documentation must be assembled from two sources:

1. **`audit_manifest.json`**: The per-atom `argument_details` array provides `name`, `kind` (positional_or_keyword, etc.), `annotation` (type), and `required`.
2. **`ghost/registry.py`**: The `REGISTRY[name]["heavy_signature"]` provides type annotations from the actual function signature.

The `dejargonized_description` field per parameter has no existing backfill source — it requires an LLM generation pass or manual authoring.

```python
# scripts/backfill_parameters.py (sketch)
for atom_entry in manifest["atoms"]:
    atom = lookup_atom(atom_entry)
    if not atom:
        continue
    for i, arg in enumerate(atom_entry.get("argument_details", [])):
        supabase.table("atom_parameters").insert({
            "atom_id": atom["atom_id"],
            "name": arg["name"],
            "position": i,
            "kind": arg.get("kind", "positional_or_keyword"),
            "type_desc": arg.get("annotation", "Any"),
            "required": arg.get("required", True),
            "default_value_repr": "",  # not in manifest; parse from atoms.py if needed
            "technical_description": "",  # derive from docstring Args section if available
            "dejargonized_description": "",  # requires LLM generation pass
            "constraints_json": "{}",
        }).execute()
```

### 5.7 Existing `fuzz_results` Table

The existing `fuzz_results` rows are migrated into `atom_audit_evidence` with `audit_type = 'fuzz_test'`:

```sql
INSERT INTO public.atom_audit_evidence (atom_id, audit_type, passed, details, source_kind, runner_version, created_at)
SELECT
    a.atom_id,
    'fuzz_test',
    fr.passed,
    jsonb_build_object(
        'strategy', fr.strategy,
        'inputs_tested', fr.inputs_tested,
        'failures', fr.failures,
        'runtime_ms', fr.runtime_ms
    ),
    'automated',
    'backfill-from-fuzz_results',
    fr.created_at
FROM public.fuzz_results fr
JOIN public.atoms a ON a.fqdn = fr.atom_fqdn;
```

### 5.8 Technical Descriptions (from `atoms.description` and docstrings)

Backfill the `technical` kind of `atom_descriptions` from the existing `atoms.description` column and the `docstring_summary` field in the audit manifest.

```python
# scripts/backfill_technical_descriptions.py (sketch)
for atom_entry in manifest["atoms"]:
    atom = lookup_atom(atom_entry)
    if not atom:
        continue
    # Prefer docstring_summary (richer); fall back to atoms.description
    content = atom_entry.get("docstring_summary", "") or atom.get("description", "")
    if not content:
        continue
    supabase.table("atom_descriptions").upsert({
        "atom_id": atom["atom_id"],
        "kind": "technical",
        "language": "en",
        "content": content,
        "generated_by": "backfill-v1",
        "reviewed": False,
        "jargon_score": 1.0,  # technical descriptions are jargon by definition
    }).execute()
```

### 5.9 Uncertainty Estimates (from `uncertainty.json` files)

```python
# scripts/backfill_uncertainty.py (sketch)
for unc_path in Path("ageoa").rglob("uncertainty.json"):
    unc_data = json.loads(unc_path.read_text())
    atom_name = unc_data.get("atom", "")
    atom = lookup_atom_by_name(atom_name)
    if not atom:
        continue
    for est in unc_data.get("estimates", []):
        supabase.table("atom_uncertainty_estimates").insert({
            "atom_id": atom["atom_id"],
            "mode": est.get("mode", "empirical"),
            "scalar_factor": est["scalar_factor"],
            "confidence": est["confidence"],
            "n_trials": est.get("n_trials", 0),
            "epsilon": est.get("epsilon", 0),
            "input_regime": est.get("input_regime", ""),
            "notes": est.get("notes", ""),
        }).execute()
```

### 5.10 Verification Matches (from `matches.json` files)

```python
# scripts/backfill_verification_matches.py (sketch)
for match_path in Path("ageoa").rglob("matches.json"):
    matches = json.loads(match_path.read_text())
    for match_result in matches:
        pdg_node = match_result.get("pdg_node", {})
        verified_match = match_result.get("verified_match")
        atom = lookup_atom_from_match(match_path, pdg_node)
        if not atom:
            continue

        candidate = verified_match["candidate"] if verified_match else {}
        decl = candidate.get("declaration", {}) if candidate else {}

        supabase.table("atom_verification_matches").insert({
            "atom_id": atom["atom_id"],
            "predicate_id": pdg_node.get("predicate_id", ""),
            "predicate_statement": pdg_node.get("statement", ""),
            "informal_desc": pdg_node.get("informal_desc", ""),
            "candidate_name": decl.get("name", ""),
            "candidate_source_lib": decl.get("source_lib", ""),
            "candidate_score": candidate.get("score"),
            "retrieval_method": candidate.get("retrieval_method", ""),
            "verified": verified_match.get("verified", False) if verified_match else False,
            "verification_level": verified_match.get("verification_level", "unverified") if verified_match else "unverified",
            "proof_term": verified_match.get("proof_term", "") if verified_match else "",
            "compiler_output": verified_match.get("compiler_output", "") if verified_match else "",
            "error_message": verified_match.get("error_message", "") if verified_match else "",
            "all_candidates": match_result.get("all_candidates", []),
            "all_verifications": match_result.get("all_verifications", []),
        }).execute()
```

### 5.11 Atom Intrinsic Fields (from audit manifest)

Backfill `source_kind`, `stateful_kind`, `is_stochastic`, `is_ffi`, and namespace/source-discovery fields on the `atoms` table from `audit_manifest.json`.

```python
# scripts/backfill_atom_intrinsic_fields.py (sketch)
for atom_entry in manifest["atoms"]:
    atom = lookup_atom(atom_entry)
    if not atom:
        continue
    supabase.table("atoms").update({
        "source_kind": atom_entry.get("source_kind", "hand_written"),
        "stateful_kind": atom_entry.get("stateful_kind", "none"),
        "is_stochastic": atom_entry.get("stochastic", False),
        "is_ffi": atom_entry.get("ffi", False),
        # Namespace/source-discovery fields derived from audit manifest
        "source_package": atom_entry.get("module_import_path", ""),
        "source_module_path": atom_entry.get("module_path", ""),
        "source_symbol": atom_entry.get("wrapper_symbol", ""),
    }).eq("atom_id", atom["atom_id"]).execute()
```

---

## Appendix: Key Decisions and Tradeoffs

| Decision | Rationale |
|---|---|
| `visibility_tier` on `atoms` + explicit entitlement grant tables | Atom access requirements and user access rights change on different timescales. Keeping both concepts explicit makes RLS easier to reason about and supports multiple grant sources. |
| JWT claims used only for coarse entitlement caching | Claims are useful for stable access hints, but contribution status can change quickly and should remain authoritative in Postgres. |
| Contribution grants expire after 12 months | Matches the product incentive design while keeping entitlement windows explicit and auditable. |
| Organization access via email-domain backed memberships | Matches the product requirement that organizations can buy access for all users under a domain without splitting the catalog into tenants. |
| `atom_audit_evidence.details` as JSONB | Audit types still have divergent evidence shapes. JSONB is flexible, but it now lives in an evidence table rather than being the only persisted audit state. |
| Separate `atom_audit_rollups` table | Serving/search should read a stable rollup layer without losing the raw evidence that produced it. |
| Publication gate via materialized `is_publishable` column | Trigger-maintained boolean avoids 5 EXISTS subqueries per row in RLS. `atom_is_publishable()` function is the computation source, not the hot path. |
| `users.effective_tier` as materialized column | Avoids 4-table UNION per row in RLS. Trigger-maintained from entitlement/role/membership/org changes. `user_effective_entitlement()` is the computation source. |
| `source_kind` and `stateful_kind` on `atoms` (not in rollups) | These are intrinsic atom properties that don't change per audit run. They belong on the atom row, not in audit findings. |
| `atom_io_specs` vs `atom_parameters` as separate tables | io_specs = conceptual data-flow ports (CDG level); parameters = Python callable signature. Overlap for leaf atoms; diverge for macro atoms with children. |
| `atom_references.ref_key` instead of `UNIQUE(atom_id, doi)` | Many existing references have `doi: NULL` and use citation keys like `"lorimer2005pulsar"`. `ref_key` is always populated (DOI or citation key). |
| Dedicated `atom_uncertainty_estimates` table | Uncertainty characterizations are quantitative measurements, not pass/fail audit results. First-class table is cleaner than JSONB in audit evidence for a scientific computing platform. |
| `atom_verification_matches` table | Pipeline-produced match artifacts (proof terms, verification levels) are catalog documentation, not just transient pipeline output. Users should see "this atom was verified against formal spec X." |
| `atom_descriptions.kind = 'technical'` | Technical descriptions live on both `atoms.description` (short, for search) and `atom_descriptions` (full, for catalog display). The `technical` kind is backfilled from docstrings. |
| `atom_descriptions.jargon_score` as a first-class field | LLM-generated descriptions are allowed, but only if they satisfy the publishable plain-language threshold. |
| `is_contributor()` and `user_effective_entitlement()` as SQL functions | Computation sources for triggers. Not called per-row in RLS (materialized columns are read instead). |
| SQLite manifest retained as local cache | Pipeline-hot code paths (`load_hyperparams_manifest_sqlite`, `load_benchmarks_sqlite`) need sub-ms reads. SQLite is generated from Supabase, not raw asyncpg. The manifest is a cache, not the source of truth. |
| Materialized view for `atom_audit_latest` | Avoids repeated `DISTINCT ON` scans. Must be refreshed after audit runs (via `REFRESH MATERIALIZED VIEW CONCURRENTLY`). |
| `search_document` and `embedding` in initial matview definition | PostgreSQL doesn't support `ALTER MATERIALIZED VIEW ADD COLUMN`. Including them upfront avoids a drop/recreate cycle. |
| Keeping `fuzz_results` table alongside `atom_audit_evidence` | Backward compatibility for Phase D ecosystem queries. New fuzz runs write to `atom_audit_evidence`; the old table is read-only. |
| `atom_versions` RLS via subquery on `atoms` | Cascading visibility: if you cannot see the atom, you cannot see its versions. Single policy, no duplication of tier logic. |
| Search index view + full-document RPC split | Matches the actual product usage: cheap high-level browse/search first, rich atom document only on demand. |
| Anonymous access via `anon` role + restricted view | PostgREST `anon` key can only see `catalog_public` view. Full atom data requires authentication. |
| Namespace/source-discovery fields as forward-looking | No current pipeline code consumes these; backfilled from `audit_manifest.json`'s `module_import_path`/`module_path`/`wrapper_symbol`. Pipeline integration is Phase 5+. |
| Device-flow trigger tolerance | `handle_new_user()` uses COALESCE for all `raw_user_meta_data` fields. Preferred path is `signInWithIdToken()` so Supabase populates metadata; trigger is the safety net. |
