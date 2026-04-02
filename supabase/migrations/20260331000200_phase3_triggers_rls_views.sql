-- Phase 3: Triggers, RLS policies, views, materialized views, and RPCs.
-- Builds the runtime access-control and serving layer on top of the
-- Phase 0/1/2 schema and backfilled data.

-- ============================================================
-- Helper functions
-- ============================================================

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
        WHEN EXISTS (
            SELECT 1 FROM active_grants WHERE entitlement_tier = 'internal'
        ) THEN 'internal'
        WHEN EXISTS (
            SELECT 1 FROM active_grants WHERE entitlement_tier = 'early_access'
        ) THEN 'early_access'
        ELSE 'general'
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
        EXISTS (
            SELECT 1 FROM public.atom_io_specs ios
            WHERE ios.atom_id = check_atom_id
        )
        AND EXISTS (
            SELECT 1 FROM public.atom_parameters p
            WHERE p.atom_id = check_atom_id
        )
        AND EXISTS (
            SELECT 1
            FROM public.atom_descriptions d
            WHERE d.atom_id = check_atom_id
              AND d.kind = 'dejargonized'
              AND d.language = 'en'
              AND d.jargon_score < 0.4
        )
        AND EXISTS (
            SELECT 1 FROM public.atom_audit_rollups ar
            WHERE ar.atom_id = check_atom_id
        )
        AND EXISTS (
            SELECT 1 FROM public.atom_references r
            WHERE r.atom_id = check_atom_id
        );
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

CREATE OR REPLACE FUNCTION public.submission_contains_fqdn(
    payload JSONB,
    target_fqdn TEXT
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN payload IS NULL THEN FALSE
        WHEN pg_catalog.jsonb_typeof(payload) = 'object' THEN payload ? target_fqdn
        WHEN pg_catalog.jsonb_typeof(payload) = 'array' THEN EXISTS (
            SELECT 1
            FROM pg_catalog.jsonb_array_elements(payload) AS elem(value)
            WHERE elem.value = pg_catalog.to_jsonb(target_fqdn)
               OR elem.value->>'fqdn' = target_fqdn
               OR elem.value->>'atom_fqdn' = target_fqdn
        )
        ELSE FALSE
    END;
$$;

CREATE OR REPLACE FUNCTION public.submission_atom_version_count(payload JSONB)
RETURNS INTEGER
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN payload IS NULL THEN 0
        WHEN pg_catalog.jsonb_typeof(payload) = 'object' THEN (
            SELECT pg_catalog.count(*)
            FROM pg_catalog.jsonb_object_keys(payload) AS obj(key)
        )::INTEGER
        WHEN pg_catalog.jsonb_typeof(payload) = 'array' THEN pg_catalog.jsonb_array_length(payload)
        ELSE 0
    END;
$$;

-- ============================================================
-- Triggers
-- ============================================================

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_io_specs ENABLE TRIGGER trg_publishable_io_specs;

DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_parameters ENABLE TRIGGER trg_publishable_parameters;

DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;
CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_descriptions ENABLE TRIGGER trg_publishable_descriptions;

DROP TRIGGER IF EXISTS trg_publishable_rollups ON public.atom_audit_rollups;
CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_audit_rollups ENABLE TRIGGER trg_publishable_rollups;

DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;
CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
ALTER TABLE public.atom_references ENABLE TRIGGER trg_publishable_references;

DROP TRIGGER IF EXISTS trg_effective_tier_grants ON public.user_entitlement_grants;
CREATE TRIGGER trg_effective_tier_grants
    AFTER INSERT OR UPDATE OR DELETE ON public.user_entitlement_grants
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_entitlement_grants ENABLE TRIGGER trg_effective_tier_grants;

DROP TRIGGER IF EXISTS trg_effective_tier_roles ON public.user_role_assignments;
CREATE TRIGGER trg_effective_tier_roles
    AFTER INSERT OR UPDATE OR DELETE ON public.user_role_assignments
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_role_assignments ENABLE TRIGGER trg_effective_tier_roles;

DROP TRIGGER IF EXISTS trg_effective_tier_org_memberships
    ON public.organization_memberships;
CREATE TRIGGER trg_effective_tier_org_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.organization_memberships
    ENABLE TRIGGER trg_effective_tier_org_memberships;

DROP TRIGGER IF EXISTS trg_effective_tier_memberships ON public.user_memberships;
CREATE TRIGGER trg_effective_tier_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.user_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
ALTER TABLE public.user_memberships ENABLE TRIGGER trg_effective_tier_memberships;

-- ============================================================
-- Enable RLS
-- ============================================================

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_email_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_entitlement_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contribution_events ENABLE ROW LEVEL SECURITY;

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
ALTER TABLE public.references_registry ENABLE ROW LEVEL SECURITY;

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
-- Policies
-- ============================================================

DROP POLICY IF EXISTS users_select_public ON public.users;
CREATE POLICY users_select_public ON public.users
    FOR SELECT USING (TRUE);

DROP POLICY IF EXISTS users_update_own ON public.users;
CREATE POLICY users_update_own ON public.users
    FOR UPDATE TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS roles_select_authenticated ON public.roles;
CREATE POLICY roles_select_authenticated ON public.roles
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS user_role_assignments_select_own ON public.user_role_assignments;
CREATE POLICY user_role_assignments_select_own ON public.user_role_assignments
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS user_entitlement_grants_select_own ON public.user_entitlement_grants;
CREATE POLICY user_entitlement_grants_select_own ON public.user_entitlement_grants
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS contribution_events_select_own ON public.contribution_events;
CREATE POLICY contribution_events_select_own ON public.contribution_events
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS contribution_events_select_internal ON public.contribution_events;
CREATE POLICY contribution_events_select_internal ON public.contribution_events
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

DROP POLICY IF EXISTS references_registry_select ON public.references_registry;
CREATE POLICY references_registry_select ON public.references_registry
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS references_registry_select_anon ON public.references_registry;
CREATE POLICY references_registry_select_anon ON public.references_registry
    FOR SELECT TO anon
    USING (TRUE);

DROP POLICY IF EXISTS organizations_select_member ON public.organizations;
CREATE POLICY organizations_select_member ON public.organizations
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.organization_memberships om
            WHERE om.organization_id = organizations.organization_id
              AND om.user_id = auth.uid()
              AND (om.ends_at IS NULL OR om.ends_at > now())
        )
    );

DROP POLICY IF EXISTS organizations_select_internal ON public.organizations;
CREATE POLICY organizations_select_internal ON public.organizations
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

DROP POLICY IF EXISTS org_email_domains_select_member ON public.organization_email_domains;
CREATE POLICY org_email_domains_select_member ON public.organization_email_domains
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.organization_memberships om
            WHERE om.organization_id = organization_email_domains.organization_id
              AND om.user_id = auth.uid()
              AND (om.ends_at IS NULL OR om.ends_at > now())
        )
    );

DROP POLICY IF EXISTS org_email_domains_select_internal ON public.organization_email_domains;
CREATE POLICY org_email_domains_select_internal ON public.organization_email_domains
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

DROP POLICY IF EXISTS organization_memberships_select_own ON public.organization_memberships;
CREATE POLICY organization_memberships_select_own ON public.organization_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS user_memberships_select_own ON public.user_memberships;
CREATE POLICY user_memberships_select_own ON public.user_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS atom_source_repositories_select_authenticated
    ON public.atom_source_repositories;
CREATE POLICY atom_source_repositories_select_authenticated
    ON public.atom_source_repositories
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS atoms_select_anon ON public.atoms;
CREATE POLICY atoms_select_anon ON public.atoms
    FOR SELECT TO anon
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

DROP POLICY IF EXISTS atoms_select_authenticated ON public.atoms;
CREATE POLICY atoms_select_authenticated ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

DROP POLICY IF EXISTS atoms_select_early_access ON public.atoms;
CREATE POLICY atoms_select_early_access ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'early_access'
        AND is_publishable = TRUE
        AND EXISTS (
            SELECT 1
            FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier IN ('early_access', 'internal')
        )
    );

DROP POLICY IF EXISTS atoms_select_internal ON public.atoms;
CREATE POLICY atoms_select_internal ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'internal'
        AND EXISTS (
            SELECT 1
            FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

DROP POLICY IF EXISTS atoms_select_own ON public.atoms;
CREATE POLICY atoms_select_own ON public.atoms
    FOR SELECT TO authenticated
    USING (owner_id = auth.uid());

DROP POLICY IF EXISTS atoms_insert_own ON public.atoms;
CREATE POLICY atoms_insert_own ON public.atoms
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS atoms_update_own ON public.atoms;
CREATE POLICY atoms_update_own ON public.atoms
    FOR UPDATE TO authenticated
    USING (owner_id = auth.uid())
    WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS atom_versions_select ON public.atom_versions;
CREATE POLICY atom_versions_select ON public.atom_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_versions.atom_id
        )
    );

DROP POLICY IF EXISTS atom_versions_insert ON public.atom_versions;
CREATE POLICY atom_versions_insert ON public.atom_versions
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_versions.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS atom_authors_select ON public.atom_authors;
CREATE POLICY atom_authors_select ON public.atom_authors
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_authors.atom_id
        )
    );

DROP POLICY IF EXISTS atom_authors_insert ON public.atom_authors;
CREATE POLICY atom_authors_insert ON public.atom_authors
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_authors.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS hyperparams_select ON public.hyperparams;
CREATE POLICY hyperparams_select ON public.hyperparams
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = hyperparams.atom_id
        )
    );

DROP POLICY IF EXISTS hyperparams_insert ON public.hyperparams;
CREATE POLICY hyperparams_insert ON public.hyperparams
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = hyperparams.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS atom_io_specs_select ON public.atom_io_specs;
CREATE POLICY atom_io_specs_select ON public.atom_io_specs
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_io_specs.atom_id
        )
    );

DROP POLICY IF EXISTS atom_io_specs_insert ON public.atom_io_specs;
CREATE POLICY atom_io_specs_insert ON public.atom_io_specs
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_io_specs.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS atom_parameters_select ON public.atom_parameters;
CREATE POLICY atom_parameters_select ON public.atom_parameters
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_parameters.atom_id
        )
    );

DROP POLICY IF EXISTS atom_parameters_insert ON public.atom_parameters;
CREATE POLICY atom_parameters_insert ON public.atom_parameters
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_parameters.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS atom_descriptions_select ON public.atom_descriptions;
CREATE POLICY atom_descriptions_select ON public.atom_descriptions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_descriptions.atom_id
        )
    );

DROP POLICY IF EXISTS atom_descriptions_insert ON public.atom_descriptions;
CREATE POLICY atom_descriptions_insert ON public.atom_descriptions
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_descriptions.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS atom_references_select ON public.atom_references;
CREATE POLICY atom_references_select ON public.atom_references
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
        )
    );

DROP POLICY IF EXISTS atom_references_insert ON public.atom_references;
CREATE POLICY atom_references_insert ON public.atom_references
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
              AND a.owner_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS audit_evidence_select ON public.atom_audit_evidence;
CREATE POLICY audit_evidence_select ON public.atom_audit_evidence
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_audit_evidence.atom_id
        )
    );

DROP POLICY IF EXISTS audit_rollups_select ON public.atom_audit_rollups;
CREATE POLICY audit_rollups_select ON public.atom_audit_rollups
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_audit_rollups.atom_id
        )
    );

DROP POLICY IF EXISTS uncertainty_estimates_select ON public.atom_uncertainty_estimates;
CREATE POLICY uncertainty_estimates_select ON public.atom_uncertainty_estimates
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_uncertainty_estimates.atom_id
        )
    );

DROP POLICY IF EXISTS verification_matches_select ON public.atom_verification_matches;
CREATE POLICY verification_matches_select ON public.atom_verification_matches
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_verification_matches.atom_id
        )
    );

DROP POLICY IF EXISTS atom_benchmarks_select ON public.atom_benchmarks;
CREATE POLICY atom_benchmarks_select ON public.atom_benchmarks
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atom_versions av
            JOIN public.atoms a ON a.atom_id = av.atom_id
            WHERE av.version_id = atom_benchmarks.version_id
        )
    );

DROP POLICY IF EXISTS bounties_select ON public.bounties;
CREATE POLICY bounties_select ON public.bounties
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS bounties_insert ON public.bounties;
CREATE POLICY bounties_insert ON public.bounties
    FOR INSERT TO authenticated
    WITH CHECK (principal_id = auth.uid());

DROP POLICY IF EXISTS bounties_update_own ON public.bounties;
CREATE POLICY bounties_update_own ON public.bounties
    FOR UPDATE TO authenticated
    USING (principal_id = auth.uid())
    WITH CHECK (principal_id = auth.uid());

DROP POLICY IF EXISTS submissions_select ON public.submissions;
CREATE POLICY submissions_select ON public.submissions
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS submissions_insert ON public.submissions;
CREATE POLICY submissions_insert ON public.submissions
    FOR INSERT TO authenticated
    WITH CHECK (architect_id = auth.uid());

DROP POLICY IF EXISTS payouts_select_own ON public.payouts;
CREATE POLICY payouts_select_own ON public.payouts
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS verification_budgets_select ON public.verification_budgets;
CREATE POLICY verification_budgets_select ON public.verification_budgets
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS verification_runs_select ON public.verification_runs;
CREATE POLICY verification_runs_select ON public.verification_runs
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS bounty_best_scores_select ON public.bounty_best_scores;
CREATE POLICY bounty_best_scores_select ON public.bounty_best_scores
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS principal_targets_select ON public.principal_targets;
CREATE POLICY principal_targets_select ON public.principal_targets
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS principal_targets_insert ON public.principal_targets;
CREATE POLICY principal_targets_insert ON public.principal_targets
    FOR INSERT TO authenticated
    WITH CHECK (set_by = auth.uid());

DROP POLICY IF EXISTS execution_receipts_select ON public.execution_receipts;
CREATE POLICY execution_receipts_select ON public.execution_receipts
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS dataset_splits_select ON public.dataset_splits;
CREATE POLICY dataset_splits_select ON public.dataset_splits
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS settlement_payouts_select ON public.settlement_payouts;
CREATE POLICY settlement_payouts_select ON public.settlement_payouts
    FOR SELECT TO authenticated
    USING (recipient_id = auth.uid()::text);

DROP POLICY IF EXISTS benchmark_suites_select ON public.benchmark_suites;
CREATE POLICY benchmark_suites_select ON public.benchmark_suites
    FOR SELECT USING (TRUE);

DROP POLICY IF EXISTS benchmark_votes_select ON public.benchmark_votes;
CREATE POLICY benchmark_votes_select ON public.benchmark_votes
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS benchmark_votes_insert ON public.benchmark_votes;
CREATE POLICY benchmark_votes_insert ON public.benchmark_votes
    FOR INSERT TO authenticated
    WITH CHECK (voter_id = auth.uid());

DROP POLICY IF EXISTS fuzz_results_select ON public.fuzz_results;
CREATE POLICY fuzz_results_select ON public.fuzz_results
    FOR SELECT USING (TRUE);

DROP POLICY IF EXISTS behavioral_equiv_select ON public.behavioral_equivalence_flags;
CREATE POLICY behavioral_equiv_select ON public.behavioral_equivalence_flags
    FOR SELECT TO authenticated
    USING (TRUE);

DROP POLICY IF EXISTS discipline_repos_select ON public.discipline_repos;
CREATE POLICY discipline_repos_select ON public.discipline_repos
    FOR SELECT USING (TRUE);

-- ============================================================
-- Views
-- ============================================================

DROP VIEW IF EXISTS public.originator_impact CASCADE;
CREATE VIEW public.originator_impact
WITH (security_invoker = true)
AS
WITH author_atoms AS (
    SELECT aa.user_id AS originator_id, aa.atom_id, a.fqdn
    FROM public.atom_authors aa
    JOIN public.atoms a ON a.atom_id = aa.atom_id
),
author_stats AS (
    SELECT originator_id, COUNT(DISTINCT atom_id) AS atom_count
    FROM author_atoms
    GROUP BY originator_id
),
originator_bounties AS (
    SELECT DISTINCT
        aa.originator_id,
        s.bounty_id
    FROM author_atoms aa
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, aa.fqdn)
     AND s.is_winner = TRUE
    JOIN public.bounties b
      ON b.bounty_id = s.bounty_id
     AND b.status = 'settled'
),
bounty_stats AS (
    SELECT
        ob.originator_id,
        COUNT(*) AS bounty_count,
        COALESCE(SUM(b.escrow_amount), 0) AS total_bounty_value
    FROM originator_bounties ob
    JOIN public.bounties b ON b.bounty_id = ob.bounty_id
    GROUP BY ob.originator_id
)
SELECT
    ast.originator_id,
    u.github_login,
    COALESCE(bst.bounty_count, 0) AS bounty_count,
    COALESCE(bst.total_bounty_value, 0) AS total_bounty_value,
    ast.atom_count
FROM author_stats ast
JOIN public.users u ON u.user_id = ast.originator_id
LEFT JOIN bounty_stats bst
  ON bst.originator_id = ast.originator_id;

DROP VIEW IF EXISTS public.compute_preserved CASCADE;
CREATE VIEW public.compute_preserved
WITH (security_invoker = true)
AS
SELECT
    b.bounty_id,
    b.escrow_amount,
    public.submission_atom_version_count(s.atom_versions) AS cdg_node_count,
    public.submission_atom_version_count(s.atom_versions) * 2000 * 5
        AS estimated_tokens_saved,
    (public.submission_atom_version_count(s.atom_versions) * 2000 * 5 * 0.003)::DOUBLE PRECISION
        AS estimated_cost_saved
FROM public.bounties b
JOIN public.submissions s
  ON s.bounty_id = b.bounty_id
 AND s.is_winner = TRUE
WHERE b.status = 'settled';

DROP VIEW IF EXISTS public.catalog_public CASCADE;
CREATE VIEW public.catalog_public
WITH (security_invoker = true)
AS
SELECT
    fqdn,
    description,
    domain_tags
FROM public.atoms
WHERE status = 'approved'
  AND visibility_tier = 'general'
  AND is_publishable = TRUE;

DROP VIEW IF EXISTS public.catalog_atoms_served CASCADE;
CREATE VIEW public.catalog_atoms_served
WITH (security_invoker = true)
AS
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
    COALESCE(ref_counts.reference_count, 0) AS reference_count
FROM public.atoms a
LEFT JOIN public.atom_descriptions d
  ON d.atom_id = a.atom_id
 AND d.kind = 'dejargonized'
 AND d.language = 'en'
LEFT JOIN public.atom_audit_rollups ar
  ON ar.atom_id = a.atom_id
LEFT JOIN (
    SELECT atom_id, COUNT(*) AS reference_count
    FROM public.atom_references
    GROUP BY atom_id
) AS ref_counts
  ON ref_counts.atom_id = a.atom_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE;

GRANT SELECT ON public.catalog_public TO anon, authenticated;
GRANT SELECT ON public.catalog_atoms_served TO authenticated;
GRANT SELECT ON public.originator_impact TO authenticated;
GRANT SELECT ON public.compute_preserved TO authenticated;

-- ============================================================
-- Materialized views
-- ============================================================

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

CREATE UNIQUE INDEX idx_audit_latest_pk
    ON public.atom_audit_latest (atom_id, audit_type);
REVOKE ALL ON TABLE public.atom_audit_latest FROM PUBLIC, anon, authenticated;

DROP MATERIALIZED VIEW IF EXISTS public.catalog_atoms_index CASCADE;
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
    setweight(to_tsvector('english', a.fqdn), 'A') ||
    setweight(to_tsvector('english', COALESCE(a.description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(d.content, '')), 'C') ||
    setweight(to_tsvector('english', array_to_string(a.domain_tags, ' ')), 'B')
        AS search_document
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
) AS ref_counts
  ON ref_counts.atom_id = a.atom_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE;

CREATE UNIQUE INDEX idx_catalog_atoms_index_pk
    ON public.catalog_atoms_index (atom_id);
CREATE INDEX idx_catalog_atoms_index_domain_tags
    ON public.catalog_atoms_index USING gin (domain_tags);
CREATE INDEX idx_catalog_atoms_index_fts
    ON public.catalog_atoms_index USING gin (search_document);
REVOKE ALL ON TABLE public.catalog_atoms_index FROM PUBLIC, anon, authenticated;

-- ============================================================
-- RPCs
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_atom_document(request_fqdn TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
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
            SELECT jsonb_agg(row_to_json(d) ORDER BY d.kind, d.language)
            FROM public.atom_descriptions d
            WHERE d.atom_id = a.atom_id
        ),
        'io_specs', (
            SELECT jsonb_agg(row_to_json(ios) ORDER BY ios.direction, ios.ordinal)
            FROM public.atom_io_specs ios
            WHERE ios.atom_id = a.atom_id
        ),
        'parameters', (
            SELECT jsonb_agg(row_to_json(p) ORDER BY p.position)
            FROM public.atom_parameters p
            WHERE p.atom_id = a.atom_id
        ),
        'references', (
            SELECT jsonb_agg(row_to_json(r) ORDER BY r.year NULLS LAST, r.title)
            FROM public.atom_references r
            WHERE r.atom_id = a.atom_id
        ),
        'audit_rollup', (
            SELECT row_to_json(ar)
            FROM public.atom_audit_rollups ar
            WHERE ar.atom_id = a.atom_id
        ),
        'audit_latest', (
            SELECT jsonb_agg(row_to_json(latest_row) ORDER BY latest_row.audit_type)
            FROM (
                SELECT DISTINCT ON (e.audit_type)
                    e.atom_id,
                    e.audit_type,
                    e.passed,
                    e.status,
                    e.details,
                    e.source_kind,
                    e.runner_version,
                    e.source_revision,
                    e.upstream_version,
                    e.created_at AS audited_at
                FROM public.atom_audit_evidence e
                WHERE e.atom_id = a.atom_id
                ORDER BY e.audit_type, e.created_at DESC
            ) AS latest_row
        ),
        'uncertainty_estimates', (
            SELECT jsonb_agg(row_to_json(ue) ORDER BY ue.created_at DESC)
            FROM public.atom_uncertainty_estimates ue
            WHERE ue.atom_id = a.atom_id
        ),
        'verification_matches', (
            SELECT jsonb_agg(
                row_to_json(vm)
                ORDER BY vm.verification_level, vm.candidate_score DESC NULLS LAST
            )
            FROM public.atom_verification_matches vm
            WHERE vm.atom_id = a.atom_id
        )
    )
    FROM public.atoms a
    WHERE a.fqdn = request_fqdn;
$$;

CREATE OR REPLACE FUNCTION public.refresh_audit_latest()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.atom_audit_latest;
END;
$$;

CREATE OR REPLACE FUNCTION public.refresh_catalog_index()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.catalog_atoms_index;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM anon;
REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM authenticated;

REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM anon;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM authenticated;

-- ============================================================
-- Initial materialized-column computation and matview refresh
-- ============================================================

UPDATE public.atoms
   SET is_publishable = public.atom_is_publishable(atom_id),
       updated_at = now();

UPDATE public.users
   SET effective_tier = public.user_effective_entitlement(user_id),
       updated_at = now();

REFRESH MATERIALIZED VIEW public.atom_audit_latest;
REFRESH MATERIALIZED VIEW public.catalog_atoms_index;
