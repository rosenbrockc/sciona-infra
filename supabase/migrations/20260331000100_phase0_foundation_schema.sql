-- Phase 0: Foundation schema for the Supabase migration.
-- Creates extensions, the full public schema graph, helper functions,
-- atom_audit_latest, and disabled maintenance triggers.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS public.users (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    github_id BIGINT UNIQUE NOT NULL,
    github_login TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    identity_tier TEXT NOT NULL DEFAULT 'contributor'
        CHECK (identity_tier IN ('contributor', 'payee')),
    stripe_account_id TEXT,
    reputation_score INTEGER NOT NULL DEFAULT 0,
    is_blacklisted BOOLEAN NOT NULL DEFAULT FALSE,
    effective_tier TEXT NOT NULL DEFAULT 'general'
        CHECK (effective_tier IN ('general', 'early_access', 'internal')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_github_login
    ON public.users (github_login);
CREATE INDEX IF NOT EXISTS idx_users_effective_tier
    ON public.users (effective_tier);

CREATE SEQUENCE IF NOT EXISTS public.synthetic_github_id_seq
    AS BIGINT
    INCREMENT BY -1
    START WITH -1
    MINVALUE -9223372036854775808
    MAXVALUE -1;

CREATE TABLE IF NOT EXISTS public.roles (
    role_name TEXT PRIMARY KEY,
    grants_tier TEXT NOT NULL
        CHECK (grants_tier IN ('general', 'early_access', 'internal')),
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS public.organizations (
    organization_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    entitlement_tier TEXT NOT NULL DEFAULT 'early_access'
        CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    membership_status TEXT NOT NULL DEFAULT 'active'
        CHECK (membership_status IN ('active', 'suspended', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_role_assignments (
    user_id UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    role_name TEXT NOT NULL REFERENCES public.roles(role_name) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by UUID REFERENCES public.users(user_id),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, role_name)
);

CREATE TABLE IF NOT EXISTS public.organization_email_domains (
    organization_id UUID NOT NULL REFERENCES public.organizations(organization_id) ON DELETE CASCADE,
    email_domain TEXT NOT NULL,
    PRIMARY KEY (organization_id, email_domain)
);

CREATE TABLE IF NOT EXISTS public.organization_memberships (
    organization_id UUID NOT NULL REFERENCES public.organizations(organization_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    membership_source TEXT NOT NULL
        CHECK (membership_source IN ('email_domain', 'manual')),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at TIMESTAMPTZ,
    PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.user_memberships (
    membership_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    membership_kind TEXT NOT NULL
        CHECK (membership_kind IN ('paid', 'complimentary')),
    entitlement_tier TEXT NOT NULL DEFAULT 'early_access'
        CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'trialing', 'past_due', 'cancelled', 'expired')),
    starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at TIMESTAMPTZ,
    stripe_customer_id TEXT NOT NULL DEFAULT '',
    stripe_subscription_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_entitlement_grants (
    grant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('role', 'paid_membership', 'organization_membership', 'contribution')),
    entitlement_tier TEXT NOT NULL
        CHECK (entitlement_tier IN ('general', 'early_access', 'internal')),
    source_ref TEXT NOT NULL DEFAULT '',
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    created_by UUID REFERENCES public.users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_entitlement_grants_user
    ON public.user_entitlement_grants (user_id);
CREATE INDEX IF NOT EXISTS idx_user_entitlement_grants_tier
    ON public.user_entitlement_grants (entitlement_tier);

CREATE TABLE IF NOT EXISTS public.contribution_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    event_kind TEXT NOT NULL
        CHECK (event_kind IN (
            'atom_authorship',
            'atom_documentation',
            'atom_uncertainty',
            'atom_reference',
            'atom_update',
            'cdg_submission',
            'bounty_win'
        )),
    entity_kind TEXT NOT NULL DEFAULT 'atom'
        CHECK (entity_kind IN ('atom', 'bounty', 'cdg')),
    entity_id UUID,
    entity_fqdn TEXT NOT NULL DEFAULT '',
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL DEFAULT 'backfill'
        CHECK (source IN ('backfill', 'git_history', 'api', 'admin')),
    source_ref TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, event_kind, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_contribution_events_user
    ON public.contribution_events (user_id);
CREATE INDEX IF NOT EXISTS idx_contribution_events_kind
    ON public.contribution_events (event_kind);
CREATE INDEX IF NOT EXISTS idx_contribution_events_approved
    ON public.contribution_events (approved_at);
CREATE INDEX IF NOT EXISTS idx_contribution_events_entity
    ON public.contribution_events (entity_kind, entity_id);

CREATE TABLE IF NOT EXISTS public.atom_source_repositories (
    source_repo_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name TEXT NOT NULL UNIQUE,
    vcs_provider TEXT NOT NULL DEFAULT 'github'
        CHECK (vcs_provider IN ('github', 'gitlab', 'other')),
    repo_url TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main',
    namespace_root TEXT NOT NULL DEFAULT 'sciona.atoms',
    namespace_path TEXT NOT NULL DEFAULT '',
    clone_priority INTEGER NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.atoms (
    atom_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fqdn TEXT UNIQUE NOT NULL,
    namespace_root TEXT NOT NULL DEFAULT 'sciona.atoms',
    namespace_path TEXT NOT NULL DEFAULT '',
    owner_id UUID NOT NULL REFERENCES public.users(user_id),
    domain_tags TEXT[] NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK (status IN ('approved', 'superseded', 'flagged', 'withdrawn')),
    superseded_by TEXT,
    visibility_tier TEXT NOT NULL DEFAULT 'general'
        CHECK (visibility_tier IN ('general', 'early_access', 'internal')),
    source_kind TEXT NOT NULL DEFAULT 'hand_written'
        CHECK (source_kind IN ('generated_ingest', 'hand_written', 'refined_ingest', 'skeleton')),
    stateful_kind TEXT NOT NULL DEFAULT 'none'
        CHECK (stateful_kind IN ('none', 'argument_state', 'explicit_state_model',
                                 'implicit_stateful', 'return_state')),
    is_stochastic BOOLEAN NOT NULL DEFAULT FALSE,
    is_ffi BOOLEAN NOT NULL DEFAULT FALSE,
    is_publishable BOOLEAN NOT NULL DEFAULT FALSE,
    source_repo_id UUID,
    source_package TEXT NOT NULL DEFAULT '',
    source_module_path TEXT NOT NULL DEFAULT '',
    source_symbol TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name = 'atoms'
          AND constraint_name = 'fk_atoms_source_repo'
    ) THEN
        ALTER TABLE public.atoms
            ADD CONSTRAINT fk_atoms_source_repo
            FOREIGN KEY (source_repo_id)
            REFERENCES public.atom_source_repositories(source_repo_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_atoms_fqdn
    ON public.atoms (fqdn);
CREATE INDEX IF NOT EXISTS idx_atoms_namespace_path
    ON public.atoms (namespace_path);
CREATE INDEX IF NOT EXISTS idx_atoms_status
    ON public.atoms (status);
CREATE INDEX IF NOT EXISTS idx_atoms_visibility_tier
    ON public.atoms (visibility_tier);
CREATE INDEX IF NOT EXISTS idx_atoms_domain_tags
    ON public.atoms USING gin (domain_tags);
CREATE INDEX IF NOT EXISTS idx_atoms_owner
    ON public.atoms (owner_id);
CREATE INDEX IF NOT EXISTS idx_atoms_source_kind
    ON public.atoms (source_kind);
CREATE INDEX IF NOT EXISTS idx_atoms_publishable
    ON public.atoms (is_publishable)
    WHERE is_publishable = TRUE;

CREATE TABLE IF NOT EXISTS public.references_registry (
    ref_id TEXT PRIMARY KEY,
    ref_type TEXT NOT NULL DEFAULT 'paper'
        CHECK (ref_type IN ('paper', 'repository', 'web', 'book', 'thesis', 'standard')),
    title TEXT NOT NULL,
    authors TEXT[] NOT NULL DEFAULT '{}',
    year INTEGER,
    venue TEXT NOT NULL DEFAULT '',
    doi TEXT,
    url TEXT NOT NULL DEFAULT '',
    bibtex_key TEXT NOT NULL DEFAULT '',
    bibtex_raw TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_references_registry_doi
    ON public.references_registry (doi)
    WHERE doi IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.atom_versions (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    content_hash TEXT UNIQUE NOT NULL,
    semver TEXT NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT FALSE,
    derives_from UUID REFERENCES public.atom_versions(version_id),
    s3_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, semver)
);

CREATE INDEX IF NOT EXISTS idx_atom_versions_atom
    ON public.atom_versions (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_versions_hash
    ON public.atom_versions (content_hash);
CREATE INDEX IF NOT EXISTS idx_atom_versions_latest
    ON public.atom_versions (atom_id)
    WHERE is_latest = TRUE;

CREATE TABLE IF NOT EXISTS public.atom_authors (
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(user_id),
    contribution_share NUMERIC(5,4) NOT NULL DEFAULT 1.0
        CHECK (contribution_share > 0 AND contribution_share <= 1),
    PRIMARY KEY (atom_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_atom_authors_user
    ON public.atom_authors (user_id);

CREATE TABLE IF NOT EXISTS public.hyperparams (
    hp_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('int', 'float', 'categorical', 'bool')),
    default_value JSONB,
    min_value JSONB,
    max_value JSONB,
    step_value JSONB,
    log_scale BOOLEAN NOT NULL DEFAULT FALSE,
    choices_json JSONB,
    constraints_json JSONB,
    semantic_role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK (status IN ('approved', 'blocked', 'deprecated')),
    UNIQUE (atom_id, name)
);

CREATE TABLE IF NOT EXISTS public.atom_io_specs (
    io_spec_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    name TEXT NOT NULL,
    type_desc TEXT NOT NULL DEFAULT 'Any',
    constraints TEXT NOT NULL DEFAULT '',
    required BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL DEFAULT 0,
    UNIQUE (atom_id, version_id, direction, name)
);

CREATE INDEX IF NOT EXISTS idx_atom_io_specs_atom
    ON public.atom_io_specs (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_io_specs_version
    ON public.atom_io_specs (version_id);

CREATE TABLE IF NOT EXISTS public.atom_parameters (
    parameter_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL
        CHECK (kind IN ('positional_only', 'positional_or_keyword', 'keyword_only', 'varargs', 'kwargs')),
    type_desc TEXT NOT NULL DEFAULT 'Any',
    required BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    technical_description TEXT NOT NULL DEFAULT '',
    dejargonized_description TEXT NOT NULL DEFAULT '',
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (atom_id, version_id, name)
);

CREATE INDEX IF NOT EXISTS idx_atom_parameters_atom
    ON public.atom_parameters (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_parameters_version
    ON public.atom_parameters (version_id);

CREATE TABLE IF NOT EXISTS public.atom_descriptions (
    description_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('technical', 'dejargonized', 'conceptual_summary', 'usage_example')),
    content TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    generated_by TEXT NOT NULL DEFAULT '',
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    jargon_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, kind, language)
);

CREATE INDEX IF NOT EXISTS idx_atom_descriptions_atom
    ON public.atom_descriptions (atom_id);

CREATE TABLE IF NOT EXISTS public.atom_references (
    reference_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    ref_id TEXT NOT NULL REFERENCES public.references_registry(ref_id) ON DELETE CASCADE,
    ref_key TEXT NOT NULL,
    doi TEXT,
    title TEXT NOT NULL,
    authors TEXT[] NOT NULL DEFAULT '{}',
    year INTEGER,
    url TEXT NOT NULL DEFAULT '',
    relevance_note TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT ''
        CHECK (confidence IN ('', 'low', 'medium', 'high')),
    matched_nodes TEXT[] NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'llm_extracted', 'crossref', 'semantic_scholar')),
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, ref_key)
);

CREATE INDEX IF NOT EXISTS idx_atom_references_atom
    ON public.atom_references (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_references_doi
    ON public.atom_references (doi)
    WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_atom_references_ref
    ON public.atom_references (ref_id);

CREATE TABLE IF NOT EXISTS public.atom_uncertainty_estimates (
    estimate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    mode TEXT NOT NULL DEFAULT 'empirical'
        CHECK (mode IN ('empirical', 'analytical', 'propagated')),
    scalar_factor DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    n_trials INTEGER NOT NULL DEFAULT 0,
    epsilon DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_regime TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_uncertainty_atom
    ON public.atom_uncertainty_estimates (atom_id);
CREATE INDEX IF NOT EXISTS idx_uncertainty_version
    ON public.atom_uncertainty_estimates (version_id);

CREATE TABLE IF NOT EXISTS public.atom_verification_matches (
    match_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    predicate_id TEXT NOT NULL DEFAULT '',
    predicate_statement TEXT NOT NULL DEFAULT '',
    informal_desc TEXT NOT NULL DEFAULT '',
    candidate_name TEXT NOT NULL DEFAULT '',
    candidate_source_lib TEXT NOT NULL DEFAULT '',
    candidate_score DOUBLE PRECISION,
    retrieval_method TEXT NOT NULL DEFAULT '',
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_level TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_level IN ('kernel_proof', 'type_checked', 'contract_checked', 'unverified')),
    proof_term TEXT NOT NULL DEFAULT '',
    compiler_output TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    all_candidates JSONB NOT NULL DEFAULT '[]',
    all_verifications JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_matches_atom
    ON public.atom_verification_matches (atom_id);
CREATE INDEX IF NOT EXISTS idx_verification_matches_version
    ON public.atom_verification_matches (version_id);
CREATE INDEX IF NOT EXISTS idx_verification_matches_level
    ON public.atom_verification_matches (verification_level);

CREATE TABLE IF NOT EXISTS public.atom_audit_evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    audit_type TEXT NOT NULL
        CHECK (audit_type IN (
            'smoke_test',
            'regression_test',
            'structural_audit',
            'semantic_audit',
            'risk_assessment',
            'parity_check',
            'fuzz_test'
        )),
    passed BOOLEAN NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    details JSONB NOT NULL DEFAULT '{}',
    source_kind TEXT NOT NULL DEFAULT 'automated'
        CHECK (source_kind IN ('automated', 'manual', 'llm_assisted')),
    runner_version TEXT NOT NULL DEFAULT '',
    run_duration_ms INTEGER,
    source_revision TEXT NOT NULL DEFAULT '',
    upstream_version TEXT NOT NULL DEFAULT '',
    review_basis_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_evidence_atom
    ON public.atom_audit_evidence (atom_id);
CREATE INDEX IF NOT EXISTS idx_audit_evidence_version
    ON public.atom_audit_evidence (version_id);
CREATE INDEX IF NOT EXISTS idx_audit_evidence_type
    ON public.atom_audit_evidence (audit_type);
CREATE INDEX IF NOT EXISTS idx_audit_evidence_atom_type
    ON public.atom_audit_evidence (atom_id, audit_type);

CREATE TABLE IF NOT EXISTS public.atom_audit_rollups (
    atom_id UUID PRIMARY KEY REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    overall_verdict TEXT NOT NULL DEFAULT 'unknown'
        CHECK (overall_verdict IN ('unknown', 'trusted', 'acceptable_with_limits',
                                   'limited_acceptability', 'misleading', 'broken')),
    structural_status TEXT NOT NULL DEFAULT 'unknown',
    runtime_status TEXT NOT NULL DEFAULT 'unknown',
    semantic_status TEXT NOT NULL DEFAULT 'unknown',
    developer_semantics_status TEXT NOT NULL DEFAULT 'unknown',
    risk_tier TEXT NOT NULL DEFAULT 'medium'
        CHECK (risk_tier IN ('low', 'medium', 'high')),
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_reasons TEXT[] NOT NULL DEFAULT '{}',
    acceptability_score INTEGER NOT NULL DEFAULT 0,
    acceptability_band TEXT NOT NULL DEFAULT 'unknown'
        CHECK (acceptability_band IN ('unknown', 'acceptable_with_limits',
                                      'acceptable_with_limits_candidate',
                                      'limited_acceptability')),
    parity_coverage_level TEXT NOT NULL DEFAULT 'unknown'
        CHECK (parity_coverage_level IN ('unknown', 'none', 'not_applicable',
                                         'positive_path', 'positive_and_negative',
                                         'parity_or_usage_equivalent')),
    parity_test_status TEXT NOT NULL DEFAULT 'unknown',
    parity_fixture_count INTEGER NOT NULL DEFAULT 0,
    parity_case_count INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'missing',
    review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_limitations TEXT[] NOT NULL DEFAULT '{}',
    review_required_actions TEXT[] NOT NULL DEFAULT '{}',
    trust_readiness TEXT NOT NULL DEFAULT 'not_ready',
    trust_blockers TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.atom_benchmarks (
    benchmark_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.atom_versions(version_id) ON DELETE CASCADE,
    benchmark_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    dataset_tag TEXT NOT NULL DEFAULT '',
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_atom_benchmarks_version
    ON public.atom_benchmarks (version_id);

CREATE TABLE IF NOT EXISTS public.bounties (
    bounty_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id UUID NOT NULL REFERENCES public.users(user_id),
    title TEXT NOT NULL,
    escrow_amount NUMERIC(12,2) NOT NULL CHECK (escrow_amount > 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'open', 'submitted', 'verified',
                          'settled', 'expired', 'cancelled')),
    deadline TIMESTAMPTZ,
    tier TEXT NOT NULL DEFAULT 'standard'
        CHECK (tier IN ('standard', 'heavy', 'gpu')),
    verification_budget INTEGER NOT NULL DEFAULT 5,
    verifications_used INTEGER NOT NULL DEFAULT 0,
    config_yml JSONB NOT NULL DEFAULT '{}',
    flare_payload JSONB,
    ageom_yml_s3 TEXT,
    dataset_s3 TEXT,
    public_split_hash TEXT,
    blind_split_hash TEXT,
    cancellation_fee NUMERIC(12,2) DEFAULT 0,
    reposted_from UUID REFERENCES public.bounties(bounty_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bounties_status
    ON public.bounties (status);
CREATE INDEX IF NOT EXISTS idx_bounties_principal
    ON public.bounties (principal_id);

CREATE TABLE IF NOT EXISTS public.submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    architect_id UUID NOT NULL REFERENCES public.users(user_id),
    cdg_hash TEXT NOT NULL,
    atom_versions JSONB NOT NULL,
    receipt_s3 TEXT NOT NULL DEFAULT '',
    receipt_json JSONB NOT NULL,
    claimed_metric_name TEXT NOT NULL,
    claimed_metric_value DOUBLE PRECISION NOT NULL,
    verified_metric_value DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending', 'receipt_valid',
                                       'public_verified', 'blind_verified', 'rejected')),
    is_winner BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_submissions_bounty
    ON public.submissions (bounty_id);
CREATE INDEX IF NOT EXISTS idx_submissions_architect
    ON public.submissions (architect_id);

CREATE TABLE IF NOT EXISTS public.payouts (
    payout_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    user_id UUID NOT NULL REFERENCES public.users(user_id),
    role TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount NUMERIC(12,2) NOT NULL,
    shapley_value TEXT,
    stripe_transfer_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'kyc_hold', 'transferred', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.verification_budgets (
    bounty_id UUID PRIMARY KEY REFERENCES public.bounties(bounty_id),
    tier TEXT NOT NULL CHECK (tier IN ('standard', 'heavy', 'gpu')),
    total_slots INT NOT NULL,
    used_slots INT NOT NULL DEFAULT 0,
    cost_per_extra NUMERIC(10,2) NOT NULL,
    overhead_deposit NUMERIC(10,2) NOT NULL DEFAULT 0,
    overhead_used NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.verification_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    submission_id UUID NOT NULL REFERENCES public.submissions(submission_id),
    split_type TEXT NOT NULL CHECK (split_type IN ('public', 'blind')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metric_values JSONB,
    output_hash TEXT,
    execution_time_s FLOAT,
    peak_memory_bytes BIGINT,
    is_deterministic BOOLEAN,
    sandbox_job_id TEXT,
    slot_consumed BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_runs_bounty
    ON public.verification_runs (bounty_id);
CREATE INDEX IF NOT EXISTS idx_verification_runs_submission
    ON public.verification_runs (submission_id);

CREATE TABLE IF NOT EXISTS public.bounty_best_scores (
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    metric_name TEXT NOT NULL,
    best_value FLOAT NOT NULL,
    best_submission_id UUID REFERENCES public.submissions(submission_id),
    is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bounty_id, metric_name)
);

CREATE TABLE IF NOT EXISTS public.principal_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    metric_name TEXT NOT NULL,
    target_value FLOAT NOT NULL,
    set_by UUID NOT NULL REFERENCES public.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_principal_targets_bounty
    ON public.principal_targets (bounty_id);

CREATE TABLE IF NOT EXISTS public.execution_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID NOT NULL REFERENCES public.submissions(submission_id),
    bounty_id UUID NOT NULL,
    cdg_hash TEXT NOT NULL,
    atom_versions JSONB NOT NULL,
    split_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value FLOAT NOT NULL,
    ageom_version TEXT NOT NULL,
    ssh_signature TEXT NOT NULL,
    ssh_public_key TEXT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    receipt_timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_execution_receipts_submission
    ON public.execution_receipts (submission_id);
CREATE INDEX IF NOT EXISTS idx_execution_receipts_bounty
    ON public.execution_receipts (bounty_id);

CREATE TABLE IF NOT EXISTS public.dataset_splits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    unit_key TEXT NOT NULL,
    partition TEXT NOT NULL CHECK (partition IN ('public', 'blind')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bounty_id, unit_key)
);

CREATE INDEX IF NOT EXISTS idx_dataset_splits_bounty
    ON public.dataset_splits (bounty_id);

CREATE TABLE IF NOT EXISTS public.settlement_payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id UUID NOT NULL REFERENCES public.bounties(bounty_id),
    recipient_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount NUMERIC(12,2) NOT NULL,
    stripe_transfer_id TEXT,
    atom_fqdn TEXT,
    cdg_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_settlement_payouts_bounty
    ON public.settlement_payouts (bounty_id);

CREATE TABLE IF NOT EXISTS public.benchmark_suites (
    benchmark_id TEXT PRIMARY KEY,
    domain_tags TEXT[] NOT NULL DEFAULT '{}',
    description TEXT,
    dataset_s3_key TEXT NOT NULL DEFAULT '',
    metric_names TEXT[] NOT NULL DEFAULT '{}',
    curation_source TEXT NOT NULL DEFAULT 'foundation'
        CHECK (curation_source IN ('foundation', 'community', 'bounty_derived')),
    proposer_id UUID REFERENCES public.users(user_id),
    vote_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
        CHECK (status IN ('active', 'retired', 'proposed')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.benchmark_votes (
    benchmark_id TEXT NOT NULL REFERENCES public.benchmark_suites(benchmark_id),
    voter_id UUID NOT NULL REFERENCES public.users(user_id),
    vote TEXT NOT NULL CHECK (vote IN ('approve', 'reject')),
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (benchmark_id, voter_id)
);

CREATE TABLE IF NOT EXISTS public.fuzz_results (
    fuzz_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_fqdn TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    strategy TEXT NOT NULL
        CHECK (strategy IN ('property_based', 'boundary_value',
                            'param_smoothing', 'behavioral_equiv')),
    passed BOOLEAN NOT NULL,
    failures JSONB DEFAULT '[]',
    inputs_tested INTEGER NOT NULL DEFAULT 0,
    runtime_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fuzz_results_atom
    ON public.fuzz_results (atom_fqdn, content_hash);

CREATE TABLE IF NOT EXISTS public.behavioral_equivalence_flags (
    flag_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_a_fqdn TEXT NOT NULL,
    atom_a_hash TEXT NOT NULL,
    atom_b_fqdn TEXT NOT NULL,
    atom_b_hash TEXT NOT NULL,
    match_ratio FLOAT NOT NULL,
    sample_size INTEGER NOT NULL,
    reviewed BOOLEAN DEFAULT FALSE,
    reviewer_id UUID REFERENCES public.users(user_id),
    disposition TEXT
        CHECK (disposition IS NULL OR disposition IN ('plagiarism', 'coincidence', 'common_algorithm')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.discipline_repos (
    repo_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_url TEXT UNIQUE NOT NULL,
    webhook_secret TEXT NOT NULL DEFAULT '',
    domain_tags TEXT[] NOT NULL DEFAULT '{}',
    maintainer_ids UUID[] NOT NULL DEFAULT '{}',
    last_synced_commit TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'removed')),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

DROP MATERIALIZED VIEW IF EXISTS public.atom_audit_latest CASCADE;
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_latest_pk
    ON public.atom_audit_latest (atom_id, audit_type);

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.users (
        user_id,
        github_id,
        github_login,
        display_name,
        avatar_url,
        email
    )
    VALUES (
        NEW.id,
        COALESCE(
            NULLIF(NEW.raw_user_meta_data->>'provider_id', '')::bigint,
            nextval('public.synthetic_github_id_seq')
        ),
        COALESCE(NEW.raw_user_meta_data->>'user_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'avatar_url', ''),
        COALESCE(NEW.email, '')
    )
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$;

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

CREATE OR REPLACE FUNCTION public.refresh_user_effective_tier()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
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

CREATE OR REPLACE FUNCTION public.refresh_atom_publishable()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
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

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

DROP TRIGGER IF EXISTS trg_effective_tier_grants ON public.user_entitlement_grants;
CREATE TRIGGER trg_effective_tier_grants
    AFTER INSERT OR UPDATE OR DELETE ON public.user_entitlement_grants
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_entitlement_grants
    DISABLE TRIGGER trg_effective_tier_grants;

DROP TRIGGER IF EXISTS trg_effective_tier_roles ON public.user_role_assignments;
CREATE TRIGGER trg_effective_tier_roles
    AFTER INSERT OR UPDATE OR DELETE ON public.user_role_assignments
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_role_assignments
    DISABLE TRIGGER trg_effective_tier_roles;

DROP TRIGGER IF EXISTS trg_effective_tier_org_memberships ON public.organization_memberships;
CREATE TRIGGER trg_effective_tier_org_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.organization_memberships
    DISABLE TRIGGER trg_effective_tier_org_memberships;

DROP TRIGGER IF EXISTS trg_effective_tier_memberships ON public.user_memberships;
CREATE TRIGGER trg_effective_tier_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.user_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_memberships
    DISABLE TRIGGER trg_effective_tier_memberships;

DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_io_specs
    DISABLE TRIGGER trg_publishable_io_specs;

DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_parameters
    DISABLE TRIGGER trg_publishable_parameters;

DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;
CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_descriptions
    DISABLE TRIGGER trg_publishable_descriptions;

DROP TRIGGER IF EXISTS trg_publishable_rollups ON public.atom_audit_rollups;
CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_audit_rollups
    DISABLE TRIGGER trg_publishable_rollups;

DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;
CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_references
    DISABLE TRIGGER trg_publishable_references;
