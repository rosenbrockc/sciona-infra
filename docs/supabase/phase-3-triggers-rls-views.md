# Phase 3: Triggers, RLS Policies, Views & Functions

## Overview

Phase 3 installs all runtime logic on top of the bare tables created in Phases 1 and 2: helper functions, materialized-column triggers, row-level security policies, catalog views, materialized views, the materialized view refresh strategy, and the full-document RPC. After this phase, the database enforces entitlement-gated visibility at the Postgres level and exposes the served catalog through views.

**Dependencies**: Phase 1 (core tables) + ALL Phase 2 sub-phases must be complete. The triggers reference tables from every Phase 2 sub-phase:
- `refresh_atom_publishable()` reads `atom_io_specs`, `atom_parameters`, `atom_descriptions`, `atom_audit_rollups`, `atom_references`
- `refresh_user_effective_tier()` reads `user_entitlement_grants`, `user_role_assignments`, `organization_memberships`, `user_memberships`
- RLS policies read materialized columns `atoms.is_publishable` and `users.effective_tier`

**Migration file**: `supabase/migrations/<timestamp>_phase3_triggers_rls_views.sql`

---

## Step 1: Helper Functions

Reference: Migration Plan Sections 2.6, 2.3.1

### 1a. is_contributor()

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

### 1b. user_effective_entitlement()

```sql
-- ============================================================
-- HELPER: user_effective_entitlement(user_uuid)
-- Returns the highest active entitlement tier for the user
-- by unioning across all four entitlement source tables.
-- ============================================================

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

### 1c. atom_is_publishable()

```sql
-- ============================================================
-- HELPER: atom_is_publishable(atom_uuid)
-- Returns TRUE when all five documentation pillars are present:
--   1. IO specs
--   2. Parameters
--   3. Dejargonized description (jargon_score < 0.4)
--   4. Audit rollup
--   5. Scholarly references
-- ============================================================

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
```

---

## Step 2: Triggers

Phase 0/1 created triggers in a disabled state so that bulk data loading in
Phase 2 would not fire expensive recomputation on every row. Phase 3 creates
the trigger functions and attaches them.

### 2a. handle_new_user() — auth user profile trigger

Auto-creates a `public.users` profile row when a new `auth.users` row is
inserted (e.g., on first OAuth sign-in).

```sql
-- ============================================================
-- TRIGGER FUNCTION: handle_new_user()
-- Auto-creates public.users profile on auth.users insert
-- ============================================================

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

-- Create or enable the trigger
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- If the trigger was created disabled in Phase 0/1, enable it:
ALTER TABLE auth.users ENABLE TRIGGER on_auth_user_created;
```

### 2b. refresh_atom_publishable() — fires on 5 pillar tables

Reference: Migration Plan Section 2.3.1

```sql
-- ============================================================
-- TRIGGER FUNCTION: refresh_atom_publishable()
-- Keeps atoms.is_publishable in sync when any of the five
-- pillar tables change.
-- ============================================================

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

-- Trigger on atom_io_specs
CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

-- Trigger on atom_parameters
CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

-- Trigger on atom_descriptions
CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

-- Trigger on atom_audit_rollups
CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

-- Trigger on atom_references
CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
```

### 2c. refresh_user_effective_tier() — fires on 4 entitlement tables

Reference: Migration Plan Section 2.6

```sql
-- ============================================================
-- TRIGGER FUNCTION: refresh_user_effective_tier()
-- Keeps users.effective_tier in sync when any entitlement
-- source table changes.
-- ============================================================

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

-- Trigger on user_entitlement_grants
CREATE TRIGGER trg_effective_tier_grants
    AFTER INSERT OR UPDATE OR DELETE ON public.user_entitlement_grants
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

-- Trigger on user_role_assignments
CREATE TRIGGER trg_effective_tier_roles
    AFTER INSERT OR UPDATE OR DELETE ON public.user_role_assignments
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

-- Trigger on organization_memberships
CREATE TRIGGER trg_effective_tier_org_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.organization_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();

-- Trigger on user_memberships
CREATE TRIGGER trg_effective_tier_memberships
    AFTER INSERT OR UPDATE OR DELETE ON public.user_memberships
    FOR EACH ROW EXECUTE FUNCTION public.refresh_user_effective_tier();
```

---

## Step 3: Enable RLS on ALL Tables

Reference: Migration Plan Section 2.7

```sql
-- ============================================================
-- ENABLE RLS ON ALL TABLES
-- ============================================================

-- Auth & users
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_email_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_entitlement_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contribution_events ENABLE ROW LEVEL SECURITY;

-- Atom registry
ALTER TABLE public.atom_source_repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atoms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_authors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hyperparams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_benchmarks ENABLE ROW LEVEL SECURITY;

-- Atom documentation (Phase 2 tables)
ALTER TABLE public.atom_io_specs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_parameters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_descriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_references ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.references_registry ENABLE ROW LEVEL SECURITY;

-- Audit
ALTER TABLE public.atom_audit_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_audit_rollups ENABLE ROW LEVEL SECURITY;

-- Uncertainty & verification
ALTER TABLE public.atom_uncertainty_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_verification_matches ENABLE ROW LEVEL SECURITY;

-- Bounties
ALTER TABLE public.bounties ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payouts ENABLE ROW LEVEL SECURITY;

-- Verification (Phase C carried forward)
ALTER TABLE public.verification_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounty_best_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.principal_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.execution_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dataset_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settlement_payouts ENABLE ROW LEVEL SECURITY;

-- Ecosystem (Phase D carried forward)
ALTER TABLE public.benchmark_suites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.benchmark_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fuzz_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.behavioral_equivalence_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discipline_repos ENABLE ROW LEVEL SECURITY;
```

---

## Step 4: RLS Policies

Reference: Migration Plan Section 2.7

### 4a. Users & entitlement tables

```sql
-- ============================================================
-- USERS
-- ============================================================

-- Anyone (including anon) can see profiles
CREATE POLICY users_select_public ON public.users
    FOR SELECT USING (true);

-- Users can update only their own profile
CREATE POLICY users_update_own ON public.users
    FOR UPDATE USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- ============================================================
-- ROLES
-- ============================================================

CREATE POLICY roles_select_authenticated ON public.roles
    FOR SELECT TO authenticated
    USING (true);

-- ============================================================
-- USER_ROLE_ASSIGNMENTS — user sees own assignments only
-- ============================================================

CREATE POLICY user_role_assignments_select_own ON public.user_role_assignments
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- USER_ENTITLEMENT_GRANTS — user sees own grants only
-- ============================================================

CREATE POLICY user_entitlement_grants_select_own ON public.user_entitlement_grants
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- CONTRIBUTION EVENTS: users can see their own contributions
CREATE POLICY contribution_events_select_own ON public.contribution_events
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- Internal users can see all contribution events
CREATE POLICY contribution_events_select_internal ON public.contribution_events
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

-- REFERENCES REGISTRY: readable by all (including anon) since references are public
CREATE POLICY references_registry_select ON public.references_registry
    FOR SELECT TO authenticated
    USING (true);

CREATE POLICY references_registry_select_anon ON public.references_registry
    FOR SELECT TO anon
    USING (true);

-- ============================================================
-- ORGANIZATIONS
-- The migration plan enables RLS on organizations but the
-- original section 2.7 did not define policies, making rows
-- invisible to non-service-role queries. The following policies
-- fill that gap with least-privilege access.
-- ============================================================

-- Members can see organizations they belong to
CREATE POLICY organizations_select_member ON public.organizations
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.organization_memberships om
            WHERE om.organization_id = organizations.organization_id
              AND om.user_id = auth.uid()
              AND (om.ends_at IS NULL OR om.ends_at > now())
        )
    );

-- Internal users (admins, founders, maintainers, staff) can see all organizations
CREATE POLICY organizations_select_internal ON public.organizations
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

-- ============================================================
-- ORGANIZATION_EMAIL_DOMAINS
-- Same gap as organizations: RLS enabled but no policies in
-- the original plan. Members can see their org's domains;
-- internal users can see all.
-- ============================================================

-- Members can see the email domains for their organization
CREATE POLICY org_email_domains_select_member ON public.organization_email_domains
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.organization_memberships om
            WHERE om.organization_id = organization_email_domains.organization_id
              AND om.user_id = auth.uid()
              AND (om.ends_at IS NULL OR om.ends_at > now())
        )
    );

-- Internal users can see all email domains
CREATE POLICY org_email_domains_select_internal ON public.organization_email_domains
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users u
            WHERE u.user_id = auth.uid()
              AND u.effective_tier = 'internal'
        )
    );

-- ============================================================
-- ORGANIZATION_MEMBERSHIPS — user sees own memberships only
-- ============================================================

CREATE POLICY organization_memberships_select_own ON public.organization_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- ============================================================
-- USER_MEMBERSHIPS — user sees own memberships only
-- ============================================================

CREATE POLICY user_memberships_select_own ON public.user_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());
```

### 4b. Atom source repositories

```sql
-- ============================================================
-- ATOM_SOURCE_REPOSITORIES — authenticated read
-- ============================================================

CREATE POLICY atom_source_repositories_select_authenticated ON public.atom_source_repositories
    FOR SELECT TO authenticated
    USING (true);
```

### 4c. Atoms — tier-gated visibility

```sql
-- ============================================================
-- ATOMS — visibility tier gated by effective entitlement
-- RLS uses materialized columns (atoms.is_publishable,
-- users.effective_tier) instead of calling functions per row.
-- ============================================================

-- Anon: only approved, general, publishable atoms
CREATE POLICY atoms_select_anon ON public.atoms
    FOR SELECT TO anon
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

-- Authenticated: all general-tier approved publishable atoms
CREATE POLICY atoms_select_authenticated ON public.atoms
    FOR SELECT TO authenticated
    USING (
        status = 'approved'
        AND visibility_tier = 'general'
        AND is_publishable = TRUE
    );

-- Early access: visible to users with early_access or internal tier
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

-- Internal: visible only to internal users
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

-- Owners see their own atoms regardless of status/tier/publishability
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
```

### 4d. Atom child tables — follow atom visibility

All atom child tables use the same pattern: SELECT is allowed if the user can see the parent atom (delegating to atoms RLS). INSERT is allowed if the user owns the parent atom.

```sql
-- ============================================================
-- ATOM_VERSIONS
-- ============================================================

CREATE POLICY atom_versions_select ON public.atom_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_versions.atom_id
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
-- ATOM_AUTHORS
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
-- HYPERPARAMS
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
-- ATOM_IO_SPECS
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
-- ATOM_PARAMETERS
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
-- ATOM_DESCRIPTIONS
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
-- ATOM_REFERENCES
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
```

### 4e. Audit evidence & rollups — read follows atom visibility, write is service-role only

```sql
-- ============================================================
-- ATOM_AUDIT_EVIDENCE — read delegated to atom RLS
-- ============================================================

CREATE POLICY audit_evidence_select ON public.atom_audit_evidence
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_evidence.atom_id
        )
    );

-- No insert/update policy for authenticated users.
-- Audit results are written exclusively by the service role
-- (backend audit pipeline). Service role bypasses RLS.

-- ============================================================
-- ATOM_AUDIT_ROLLUPS — read delegated to atom RLS
-- ============================================================

CREATE POLICY audit_rollups_select ON public.atom_audit_rollups
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_rollups.atom_id
        )
    );
```

### 4f. Uncertainty estimates & verification matches

```sql
-- ============================================================
-- ATOM_UNCERTAINTY_ESTIMATES — read follows atom visibility
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
-- ATOM_VERIFICATION_MATCHES — read follows atom visibility
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
-- ATOM_BENCHMARKS — read follows atom version visibility
-- ============================================================

CREATE POLICY atom_benchmarks_select ON public.atom_benchmarks
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atom_versions av
            WHERE av.version_id = atom_benchmarks.version_id
        )
    );
```

### 4g. Bounties, submissions, payouts

```sql
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
-- SUBMISSIONS — visible to all authenticated, writable by architect
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
```

### 4h. Verification tables (Phase C carried forward)

```sql
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
```

### 4i. Ecosystem tables (Phase D carried forward)

```sql
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

---

## Step 5: Views

Reference: Migration Plan Sections 2.5, 2.8

### 5a. originator_impact — dashboard view

Shows per-originator statistics: how many atoms they authored, how many bounties those atoms contributed to winning, and the total bounty value.

```sql
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
```

### 5b. compute_preserved — dashboard view

Shows estimated compute savings from reused atoms in winning submissions.

```sql
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

### 5c. catalog_public — minimal anon-accessible view

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

### 5d. catalog_atoms_served — rich authenticated view

```sql
-- Authenticated served catalog with joined documentation bundle
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

This view inherits RLS from the underlying `atoms` table -- authenticated users will only see atoms their entitlement tier permits.

---

## Step 6: Materialized Views

### 6a. atom_audit_latest

Shows the most recent audit evidence row per (atom, audit_type) pair. Used by
the `get_atom_document()` RPC and by audit pipelines to check current state.

```sql
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
```

### 6b. catalog_atoms_index — search index materialized view

The primary search index for the catalog. Includes full-text search document
and a placeholder for embedding vectors (populated by a separate backfill job).

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

**Note**: PostgreSQL does not support `ALTER MATERIALIZED VIEW ADD COLUMN`. The `search_document` and `embedding` columns are included in the initial definition to avoid a drop/recreate cycle later.

---

## Step 7: get_atom_document() RPC Function

Reference: Migration Plan Section 2.8.1

**Security recommendation**: The migration plan specifies `SECURITY DEFINER` for this function. We use `SECURITY INVOKER` instead for defense in depth. With `SECURITY INVOKER`, the function executes with the caller's permissions, meaning RLS on the underlying tables is still enforced. If the caller cannot see the atom (e.g., an anon user trying to access an early_access atom), the function returns NULL rather than leaking data through a DEFINER bypass.

```sql
-- ============================================================
-- RPC: get_atom_document(fqdn)
-- Assembles the full atom documentation bundle as a single
-- JSONB response. Called on-demand for detail views.
--
-- SECURITY INVOKER: RLS is enforced on all underlying tables.
-- If the caller cannot see the atom, the function returns NULL.
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
            SELECT jsonb_agg(row_to_json(al) ORDER BY al.audit_type)
            FROM public.atom_audit_latest al
            WHERE al.atom_id = a.atom_id
        ),
        'uncertainty_estimates', (
            SELECT jsonb_agg(row_to_json(ue) ORDER BY ue.created_at DESC)
            FROM public.atom_uncertainty_estimates ue
            WHERE ue.atom_id = a.atom_id
        ),
        'verification_matches', (
            SELECT jsonb_agg(row_to_json(vm) ORDER BY vm.verification_level, vm.candidate_score DESC)
            FROM public.atom_verification_matches vm
            WHERE vm.atom_id = a.atom_id
        )
    )
    FROM public.atoms a
    WHERE a.fqdn = request_fqdn;
$$;
```

---

## Step 8: Materialized View Refresh Strategy

Two materialized views require periodic refresh: `atom_audit_latest` and `catalog_atoms_index`. Both use `REFRESH MATERIALIZED VIEW CONCURRENTLY` so reads are not blocked during refresh. This requires the unique indexes created in Step 6.

### 8a. Refresh RPC wrappers

Expose refresh operations as RPCs callable by the service role only.

```sql
-- ============================================================
-- Refresh atom_audit_latest
-- ============================================================

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

-- Only service role should call this
REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM public;
REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM anon;
REVOKE EXECUTE ON FUNCTION public.refresh_audit_latest() FROM authenticated;

-- ============================================================
-- Refresh catalog_atoms_index
-- ============================================================

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

REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM public;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM anon;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM authenticated;
```

### 8b. When to refresh

| Trigger event | Target views | Method |
|---|---|---|
| Audit pipeline completes (new evidence rows + rollup recompute) | `atom_audit_latest`, then `catalog_atoms_index` | Call `refresh_audit_latest()` then `refresh_catalog_index()` via service role |
| Backfill script completes (Phase 2 or later) | Both views | Same as above |
| Atom approved / descriptions updated / references added | `catalog_atoms_index` | Call `refresh_catalog_index()` via service role |
| Manual admin action | Both views | SQL editor or service role RPC |
| Cron fallback (catches any missed events) | Both views | `pg_cron` every 15 minutes |

### 8c. Application integration

```python
# At end of audit pipeline run (Python sketch)
supabase.rpc("refresh_audit_latest").execute()
supabase.rpc("refresh_catalog_index").execute()
```

### 8d. pg_cron fallback (optional, requires Supabase Pro)

```sql
-- Requires pg_cron extension
SELECT cron.schedule(
    'refresh-audit-latest',
    '*/15 * * * *',
    $$REFRESH MATERIALIZED VIEW CONCURRENTLY public.atom_audit_latest$$
);

SELECT cron.schedule(
    'refresh-catalog-index',
    '*/15 * * * *',
    $$REFRESH MATERIALIZED VIEW CONCURRENTLY public.catalog_atoms_index$$
);
```

---

## Step 9: Initial Materialized Column Computation

After all triggers are in place, run a one-time backfill to set `atoms.is_publishable` and `users.effective_tier` for all existing rows. The triggers will keep them in sync going forward.

```sql
-- ============================================================
-- ONE-TIME BACKFILL: atoms.is_publishable
-- ============================================================

UPDATE public.atoms
   SET is_publishable = public.atom_is_publishable(atom_id),
       updated_at = now();

-- ============================================================
-- ONE-TIME BACKFILL: users.effective_tier
-- ============================================================

UPDATE public.users
   SET effective_tier = public.user_effective_entitlement(user_id),
       updated_at = now();
```

**Performance note**: For the initial 505 atoms, this completes in seconds. For users, the 4-table UNION runs once per user row. If the user count is large, batch in chunks of 1000:

```sql
-- Batched alternative for large user tables:
UPDATE public.users
   SET effective_tier = public.user_effective_entitlement(user_id),
       updated_at = now()
 WHERE user_id IN (
     SELECT user_id FROM public.users
     ORDER BY user_id
     LIMIT 1000 OFFSET 0
 );
-- Repeat with increasing OFFSET until all rows are updated.
```

---

## Step 10: Validation Queries

### 10a. Prerequisites

- A Supabase project with Phase 1 + Phase 2 schema applied
- Seed data: at least 3 atoms with varying `visibility_tier` (`general`, `early_access`, `internal`), at least 1 fully publishable and 1 incomplete
- 4 test contexts:
  - `user_anon`: no auth (use anon key)
  - `user_general`: authenticated, `effective_tier = 'general'`
  - `user_early`: authenticated, `effective_tier = 'early_access'`
  - `user_internal`: authenticated, `effective_tier = 'internal'`

### 10b. Verify schema objects exist

```sql
-- Functions exist
SELECT proname FROM pg_proc
WHERE proname IN ('is_contributor', 'user_effective_entitlement', 'atom_is_publishable',
                   'refresh_atom_publishable', 'refresh_user_effective_tier',
                   'handle_new_user', 'get_atom_document',
                   'refresh_audit_latest', 'refresh_catalog_index')
  AND pronamespace = 'public'::regnamespace
ORDER BY proname;
-- Expected: 9 rows

-- Triggers are enabled
SELECT tgname, tgenabled
FROM pg_trigger
WHERE tgname LIKE 'trg_%' OR tgname = 'on_auth_user_created'
ORDER BY tgname;
-- Expected: all show 'O' (origin-enabled)

-- RLS is enabled on all tables
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
-- Expected: all show rowsecurity = true

-- Policies exist
SELECT tablename, policyname
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
-- Expected: ~55 policies
```

### 10c. Test as `anon` role

```sql
SET ROLE anon;

-- Should see only general, publishable atoms
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE visibility_tier = 'general') AS general_count,
       COUNT(*) FILTER (WHERE visibility_tier != 'general') AS other_count
FROM public.atoms;
-- Expected: other_count = 0

-- catalog_public should work
SELECT COUNT(*) FROM public.catalog_public;
-- Expected: matches general publishable count

-- Should NOT see roles (authenticated only)
SELECT COUNT(*) FROM public.roles;
-- Expected: 0

-- Should NOT see bounties (authenticated only)
SELECT COUNT(*) FROM public.bounties;
-- Expected: 0

-- Should NOT see organizations
SELECT COUNT(*) FROM public.organizations;
-- Expected: 0

-- Users are visible (public profiles)
SELECT COUNT(*) FROM public.users;
-- Expected: total user count

RESET ROLE;
```

### 10d. Test as `authenticated` role (general tier)

```sql
SET ROLE authenticated;
SET request.jwt.claims = '{"sub": "<general-user-uuid>"}';

-- Should see general atoms + own atoms only
SELECT visibility_tier, COUNT(*)
FROM public.atoms
GROUP BY visibility_tier;
-- Expected: only 'general' rows (plus any owned by this user)

-- Should NOT see early_access atoms (unless owner)
SELECT COUNT(*) FROM public.atoms
WHERE visibility_tier = 'early_access' AND owner_id != auth.uid();
-- Expected: 0

-- Should see own entitlement grants only
SELECT COUNT(*) FROM public.user_entitlement_grants;
-- Expected: only this user's grants

-- Should see own payouts only
SELECT COUNT(*) FROM public.payouts;
-- Expected: only this user's payouts

-- Should see bounties
SELECT COUNT(*) FROM public.bounties;
-- Expected: total bounty count

-- Organizations: only if member
SELECT COUNT(*) FROM public.organizations;
-- Expected: 0 (general user is typically not an org member)

-- Child table cascading: cannot see early_access atom's children
SELECT COUNT(*) FROM public.atom_io_specs
WHERE atom_id IN (
    SELECT atom_id FROM public.atoms
    WHERE visibility_tier = 'early_access' AND owner_id != auth.uid()
);
-- Expected: 0

-- get_atom_document for a general atom should work
SELECT public.get_atom_document('<general-atom-fqdn>') IS NOT NULL AS has_doc;
-- Expected: true

-- get_atom_document for an early_access atom should return NULL
SELECT public.get_atom_document('<early-access-atom-fqdn>') IS NULL AS blocked;
-- Expected: true (SECURITY INVOKER enforces RLS)

RESET ROLE;
```

### 10e. Test as `authenticated` role (early_access tier)

```sql
SET ROLE authenticated;
SET request.jwt.claims = '{"sub": "<early-access-user-uuid>"}';

-- Should see general AND early_access atoms
SELECT visibility_tier, COUNT(*)
FROM public.atoms
WHERE status = 'approved' AND is_publishable = TRUE
GROUP BY visibility_tier;
-- Expected: 'general' and 'early_access' rows

-- Should NOT see internal atoms
SELECT COUNT(*) FROM public.atoms
WHERE visibility_tier = 'internal' AND owner_id != auth.uid();
-- Expected: 0

-- get_atom_document should work for early_access atoms
SELECT public.get_atom_document('<early-access-atom-fqdn>') IS NOT NULL AS has_doc;
-- Expected: true

-- get_atom_document should return NULL for internal atoms
SELECT public.get_atom_document('<internal-atom-fqdn>') IS NULL AS blocked;
-- Expected: true

-- catalog_atoms_served should show general + early_access
SELECT COUNT(*) FROM public.catalog_atoms_served;
-- Expected: general + early_access publishable count

RESET ROLE;
```

### 10f. Test as `authenticated` role (internal tier)

```sql
SET ROLE authenticated;
SET request.jwt.claims = '{"sub": "<internal-user-uuid>"}';

-- Should see all visibility tiers
SELECT visibility_tier, COUNT(*)
FROM public.atoms
WHERE status = 'approved' AND is_publishable = TRUE
GROUP BY visibility_tier;
-- Expected: 'general', 'early_access', and 'internal' rows

-- Should see all organizations (internal policy)
SELECT COUNT(*) FROM public.organizations;
-- Expected: total org count

-- Should see all email domains (internal policy)
SELECT COUNT(*) FROM public.organization_email_domains;
-- Expected: total domain count

-- get_atom_document should work for internal atoms
SELECT public.get_atom_document('<internal-atom-fqdn>') IS NOT NULL AS has_doc;
-- Expected: true

RESET ROLE;
```

### 10g. Test trigger behavior

```sql
-- Test effective_tier trigger:
-- As service role, insert a grant for a general user
INSERT INTO public.user_entitlement_grants (user_id, source_kind, entitlement_tier, source_ref)
VALUES ('<general-user-uuid>', 'contribution', 'early_access', 'test-grant');

SELECT effective_tier FROM public.users WHERE user_id = '<general-user-uuid>';
-- Expected: 'early_access'

-- Clean up
DELETE FROM public.user_entitlement_grants
WHERE user_id = '<general-user-uuid>' AND source_ref = 'test-grant';

SELECT effective_tier FROM public.users WHERE user_id = '<general-user-uuid>';
-- Expected: 'general' (reverted)

-- Test publishability trigger:
-- Delete references for a publishable atom
DELETE FROM public.atom_references WHERE atom_id = '<publishable-atom-uuid>';

SELECT is_publishable FROM public.atoms WHERE atom_id = '<publishable-atom-uuid>';
-- Expected: FALSE

-- Re-insert the reference
INSERT INTO public.atom_references (atom_id, ref_key, title)
VALUES ('<publishable-atom-uuid>', 'test-ref', 'Test Reference');

SELECT is_publishable FROM public.atoms WHERE atom_id = '<publishable-atom-uuid>';
-- Expected: TRUE
```

### 10h. Test materialized column consistency

```sql
-- Verify no stale is_publishable values
SELECT COUNT(*) FROM public.atoms
WHERE is_publishable != public.atom_is_publishable(atom_id);
-- Expected: 0

-- Verify no stale effective_tier values
SELECT COUNT(*) FROM public.users
WHERE effective_tier != public.user_effective_entitlement(user_id);
-- Expected: 0
```

### 10i. Test INSERT/UPDATE restrictions

```sql
SET ROLE authenticated;
SET request.jwt.claims = '{"sub": "<some-user-uuid>"}';

-- Should NOT be able to insert an atom owned by someone else
INSERT INTO public.atoms (fqdn, owner_id, status, visibility_tier)
VALUES ('test.someone.else', '<different-user-uuid>', 'approved', 'general');
-- Expected: ERROR (RLS violation)

-- Should NOT be able to update another user's atom
UPDATE public.atoms SET description = 'hacked'
WHERE owner_id != auth.uid();
-- Expected: 0 rows affected

-- Should NOT be able to insert audit evidence
INSERT INTO public.atom_audit_evidence (atom_id, audit_type, passed)
VALUES ('<some-atom-uuid>', 'smoke_test', true);
-- Expected: ERROR (no INSERT policy for authenticated)

RESET ROLE;
```

---

## Testing Summary

| Test case | Role | Expected result |
|-----------|------|-----------------|
| **T1**: SELECT from `atoms` | anon | Only approved + general + publishable atoms |
| **T2**: SELECT from `atoms` | general | Same as T1, plus own atoms regardless of status |
| **T3**: SELECT from `atoms` | early_access | T2 + early_access publishable atoms |
| **T4**: SELECT from `atoms` | internal | T3 + internal publishable atoms |
| **T5**: SELECT from `atoms` where `is_publishable = FALSE` | general | Only own atoms (if any) |
| **T6**: SELECT from `atoms` where `is_publishable = FALSE` | internal | Only own atoms (internal does NOT see unpublished atoms they do not own) |
| **T7**: SELECT from `atom_io_specs` for a general atom | anon | Returns rows (inherits atom visibility) |
| **T8**: SELECT from `atom_io_specs` for an early_access atom | general | Returns 0 rows |
| **T9**: SELECT from `atom_io_specs` for an early_access atom | early_access | Returns rows |
| **T10**: SELECT from `payouts` | general | Only own payouts |
| **T11**: SELECT from `user_entitlement_grants` | general | Only own grants |
| **T12**: INSERT into `atoms` with `owner_id != auth.uid()` | authenticated | Rejected |
| **T13**: UPDATE another user's atom | authenticated | Rejected |
| **T14**: INSERT into `atom_audit_evidence` | authenticated | Rejected (service role only) |
| **T15**: SELECT from `catalog_public` | anon | Returns general publishable atoms |
| **T16**: SELECT from `catalog_atoms_served` | early_access | Returns general + early_access atoms |
| **T17**: Call `get_atom_document('some.general.atom')` | general | Returns full JSONB bundle |
| **T18**: Call `get_atom_document('some.early_access.atom')` | general | Returns NULL (SECURITY INVOKER enforces RLS) |
| **T19**: SELECT from `organizations` | general (non-member) | Returns 0 rows |
| **T20**: SELECT from `organizations` | internal | Returns all organizations |
| **T21**: SELECT from `organization_email_domains` | member of org | Returns own org's domains only |
| **T22**: SELECT from `organization_email_domains` | internal | Returns all domains |

| Trigger test | Action | Expected result |
|---|---|---|
| **T30**: Insert all 5 pillar rows for an atom | Service role | `atoms.is_publishable` becomes TRUE |
| **T31**: Delete the `atom_references` row for that atom | Service role | `atoms.is_publishable` becomes FALSE |
| **T32**: Re-insert the reference | Service role | `atoms.is_publishable` becomes TRUE again |
| **T33**: Insert `user_role_assignments` with role `Administrator` | Service role | `users.effective_tier` becomes `'internal'` |
| **T34**: Delete that role assignment | Service role | `users.effective_tier` drops to next highest or `'general'` |
| **T35**: Insert `user_entitlement_grants` with `early_access` | Service role | `users.effective_tier` becomes `'early_access'` |
| **T36**: Set `expires_at` to the past on that grant | Service role | `users.effective_tier` becomes `'general'` |

---

## Validation Criteria

Phase 3 is complete when all of the following hold:

1. **Functions exist and return correct values**:
   - `is_contributor(user_id)` returns TRUE for users who authored approved atoms, FALSE otherwise
   - `user_effective_entitlement(user_id)` returns the correct highest tier across all 4 source tables
   - `atom_is_publishable(atom_id)` returns TRUE only when all 5 pillars are present

2. **Triggers fire correctly**:
   - `on_auth_user_created` creates a `public.users` profile row on auth user insert
   - Inserting/updating/deleting any of the 5 pillar tables updates `atoms.is_publishable`
   - Inserting/updating/deleting any of the 4 entitlement tables updates `users.effective_tier`
   - No infinite trigger loops (the trigger functions update `atoms`/`users` directly, not the pillar tables)

3. **RLS enforces visibility**:
   - Anonymous users see only general + publishable atoms
   - General authenticated users see general + publishable + own atoms
   - Early access users additionally see early_access + publishable atoms
   - Internal users additionally see internal + publishable atoms
   - No user can see another user's payouts, entitlement grants, role assignments, or memberships
   - Organizations and email domains are visible only to members and internal users
   - No authenticated user can INSERT audit evidence (service role only)
   - All child table SELECT policies correctly delegate to atom visibility

4. **Views return correct data**:
   - `catalog_public` returns only general publishable atoms with 3 columns (fqdn, description, domain_tags)
   - `catalog_atoms_served` returns atoms visible to the current user with the joined documentation columns
   - `originator_impact` returns per-originator statistics
   - `compute_preserved` returns per-bounty compute savings estimates
   - `anon` role has SELECT on `catalog_public`

5. **Materialized views are populated**:
   - `atom_audit_latest` contains distinct-on rows per (atom, audit_type)
   - `catalog_atoms_index` contains approved publishable atoms with search documents
   - `refresh_audit_latest()` and `refresh_catalog_index()` execute without error

6. **RPC works end-to-end**:
   - `get_atom_document('existing.fqdn')` returns a complete JSONB bundle with all 10 keys
   - `get_atom_document('nonexistent.fqdn')` returns NULL
   - `get_atom_document('inaccessible.fqdn')` returns NULL when caller lacks tier (SECURITY INVOKER)

7. **Materialized columns are backfilled**:
   - No atom has stale `is_publishable` (verified by `SELECT count(*) FROM atoms WHERE is_publishable != atom_is_publishable(atom_id)` returning 0)
   - No user has stale `effective_tier` (verified by `SELECT count(*) FROM users WHERE effective_tier != user_effective_entitlement(user_id)` returning 0)

---

## Rollback Procedure

Rollback drops all objects created in this phase in reverse dependency order. Tables and columns are NOT dropped (those belong to Phase 1/2).

```sql
-- ============================================================
-- ROLLBACK: Phase 3
-- Run in a single transaction.
-- ============================================================

BEGIN;

-- 1. Remove pg_cron jobs if created
SELECT cron.unschedule('refresh-audit-latest');
SELECT cron.unschedule('refresh-catalog-index');
-- (Ignore errors if pg_cron is not installed or jobs don't exist)

-- 2. Drop materialized view refresh functions
DROP FUNCTION IF EXISTS public.refresh_audit_latest();
DROP FUNCTION IF EXISTS public.refresh_catalog_index();

-- 3. Drop materialized views
DROP MATERIALIZED VIEW IF EXISTS public.catalog_atoms_index;
DROP MATERIALIZED VIEW IF EXISTS public.atom_audit_latest;

-- 4. Drop views
DROP VIEW IF EXISTS public.catalog_atoms_served;
DROP VIEW IF EXISTS public.catalog_public;
DROP VIEW IF EXISTS public.originator_impact;
DROP VIEW IF EXISTS public.compute_preserved;

-- 5. Drop RPC function
DROP FUNCTION IF EXISTS public.get_atom_document(TEXT);

-- 6. Drop all RLS policies (grouped by table)
-- Users & entitlement tables
DROP POLICY IF EXISTS users_select_public ON public.users;
DROP POLICY IF EXISTS users_update_own ON public.users;
DROP POLICY IF EXISTS roles_select_authenticated ON public.roles;
DROP POLICY IF EXISTS user_role_assignments_select_own ON public.user_role_assignments;
DROP POLICY IF EXISTS user_entitlement_grants_select_own ON public.user_entitlement_grants;
DROP POLICY IF EXISTS organizations_select_member ON public.organizations;
DROP POLICY IF EXISTS organizations_select_internal ON public.organizations;
DROP POLICY IF EXISTS org_email_domains_select_member ON public.organization_email_domains;
DROP POLICY IF EXISTS org_email_domains_select_internal ON public.organization_email_domains;
DROP POLICY IF EXISTS organization_memberships_select_own ON public.organization_memberships;
DROP POLICY IF EXISTS user_memberships_select_own ON public.user_memberships;

-- Atom source repos
DROP POLICY IF EXISTS atom_source_repositories_select_authenticated ON public.atom_source_repositories;

-- Atoms
DROP POLICY IF EXISTS atoms_select_anon ON public.atoms;
DROP POLICY IF EXISTS atoms_select_authenticated ON public.atoms;
DROP POLICY IF EXISTS atoms_select_early_access ON public.atoms;
DROP POLICY IF EXISTS atoms_select_internal ON public.atoms;
DROP POLICY IF EXISTS atoms_select_own ON public.atoms;
DROP POLICY IF EXISTS atoms_insert_own ON public.atoms;
DROP POLICY IF EXISTS atoms_update_own ON public.atoms;

-- Atom child tables
DROP POLICY IF EXISTS atom_versions_select ON public.atom_versions;
DROP POLICY IF EXISTS atom_versions_insert ON public.atom_versions;
DROP POLICY IF EXISTS atom_authors_select ON public.atom_authors;
DROP POLICY IF EXISTS atom_authors_insert ON public.atom_authors;
DROP POLICY IF EXISTS hyperparams_select ON public.hyperparams;
DROP POLICY IF EXISTS hyperparams_insert ON public.hyperparams;
DROP POLICY IF EXISTS atom_io_specs_select ON public.atom_io_specs;
DROP POLICY IF EXISTS atom_io_specs_insert ON public.atom_io_specs;
DROP POLICY IF EXISTS atom_parameters_select ON public.atom_parameters;
DROP POLICY IF EXISTS atom_parameters_insert ON public.atom_parameters;
DROP POLICY IF EXISTS atom_descriptions_select ON public.atom_descriptions;
DROP POLICY IF EXISTS atom_descriptions_insert ON public.atom_descriptions;
DROP POLICY IF EXISTS atom_references_select ON public.atom_references;
DROP POLICY IF EXISTS atom_references_insert ON public.atom_references;

-- Audit
DROP POLICY IF EXISTS audit_evidence_select ON public.atom_audit_evidence;
DROP POLICY IF EXISTS audit_rollups_select ON public.atom_audit_rollups;

-- Uncertainty & verification
DROP POLICY IF EXISTS uncertainty_estimates_select ON public.atom_uncertainty_estimates;
DROP POLICY IF EXISTS verification_matches_select ON public.atom_verification_matches;
DROP POLICY IF EXISTS atom_benchmarks_select ON public.atom_benchmarks;

-- Bounties
DROP POLICY IF EXISTS bounties_select ON public.bounties;
DROP POLICY IF EXISTS bounties_insert ON public.bounties;
DROP POLICY IF EXISTS bounties_update_own ON public.bounties;
DROP POLICY IF EXISTS submissions_select ON public.submissions;
DROP POLICY IF EXISTS submissions_insert ON public.submissions;
DROP POLICY IF EXISTS payouts_select_own ON public.payouts;

-- Verification tables
DROP POLICY IF EXISTS verification_budgets_select ON public.verification_budgets;
DROP POLICY IF EXISTS verification_runs_select ON public.verification_runs;
DROP POLICY IF EXISTS bounty_best_scores_select ON public.bounty_best_scores;
DROP POLICY IF EXISTS principal_targets_select ON public.principal_targets;
DROP POLICY IF EXISTS principal_targets_insert ON public.principal_targets;
DROP POLICY IF EXISTS execution_receipts_select ON public.execution_receipts;
DROP POLICY IF EXISTS dataset_splits_select ON public.dataset_splits;
DROP POLICY IF EXISTS settlement_payouts_select ON public.settlement_payouts;

-- Ecosystem
DROP POLICY IF EXISTS benchmark_suites_select ON public.benchmark_suites;
DROP POLICY IF EXISTS benchmark_votes_select ON public.benchmark_votes;
DROP POLICY IF EXISTS benchmark_votes_insert ON public.benchmark_votes;
DROP POLICY IF EXISTS fuzz_results_select ON public.fuzz_results;
DROP POLICY IF EXISTS behavioral_equiv_select ON public.behavioral_equivalence_flags;
DROP POLICY IF EXISTS discipline_repos_select ON public.discipline_repos;

-- 7. Disable RLS on all tables
ALTER TABLE public.users DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.roles DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_role_assignments DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_email_domains DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_memberships DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_memberships DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_entitlement_grants DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_source_repositories DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atoms DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_versions DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_authors DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.hyperparams DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_benchmarks DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_io_specs DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_parameters DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_descriptions DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_references DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_audit_evidence DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_audit_rollups DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_uncertainty_estimates DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_verification_matches DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounties DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.payouts DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_budgets DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_runs DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounty_best_scores DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.principal_targets DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.execution_receipts DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.dataset_splits DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.settlement_payouts DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.benchmark_suites DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.benchmark_votes DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.fuzz_results DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.behavioral_equivalence_flags DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.discipline_repos DISABLE ROW LEVEL SECURITY;

-- 8. Drop triggers (publishable)
DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;
DROP TRIGGER IF EXISTS trg_publishable_rollups ON public.atom_audit_rollups;
DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;

-- 9. Drop triggers (effective tier)
DROP TRIGGER IF EXISTS trg_effective_tier_grants ON public.user_entitlement_grants;
DROP TRIGGER IF EXISTS trg_effective_tier_roles ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_effective_tier_org_memberships ON public.organization_memberships;
DROP TRIGGER IF EXISTS trg_effective_tier_memberships ON public.user_memberships;

-- 10. Drop auth trigger
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

-- 11. Drop trigger functions
DROP FUNCTION IF EXISTS public.refresh_atom_publishable();
DROP FUNCTION IF EXISTS public.refresh_user_effective_tier();
DROP FUNCTION IF EXISTS public.handle_new_user();

-- 12. Drop helper functions
DROP FUNCTION IF EXISTS public.atom_is_publishable(UUID);
DROP FUNCTION IF EXISTS public.user_effective_entitlement(UUID);
DROP FUNCTION IF EXISTS public.is_contributor(UUID);

-- 13. Reset materialized columns to defaults
UPDATE public.atoms SET is_publishable = FALSE;
UPDATE public.users SET effective_tier = 'general';

-- 14. Revoke grants
REVOKE SELECT ON public.catalog_public FROM anon;

COMMIT;
```

---

## Execution Order Summary

| Step | Description | Depends on |
|------|-------------|------------|
| 1 | Create helper functions (`is_contributor`, `user_effective_entitlement`, `atom_is_publishable`) | Phase 1 + Phase 2 tables |
| 2 | Create trigger functions + attach triggers to 10 tables (`auth.users` + 5 pillar + 4 entitlement) | Step 1 |
| 3 | Enable RLS on all 35 tables | Phase 1 + Phase 2 tables |
| 4 | Create all RLS policies (~57 policies) | Step 3 |
| 5 | Create views (`originator_impact`, `compute_preserved`, `catalog_public`, `catalog_atoms_served`) | Steps 3-4 |
| 6 | Create materialized views (`atom_audit_latest`, `catalog_atoms_index`) | Phase 2 data |
| 7 | Create `get_atom_document()` RPC | Step 6 (depends on `atom_audit_latest`) |
| 8 | Create refresh RPC wrappers + optional pg_cron jobs | Step 6 |
| 9 | Run initial materialized column backfill | Steps 1-2 |
| 10 | Run validation queries | Steps 1-9 |
