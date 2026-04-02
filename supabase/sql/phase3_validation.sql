-- Validation queries for Phase 3 runtime schema objects.

SELECT proname
FROM pg_proc
WHERE proname IN (
    'is_contributor',
    'user_effective_entitlement',
    'atom_is_publishable',
    'refresh_atom_publishable',
    'refresh_user_effective_tier',
    'handle_new_user',
    'submission_contains_fqdn',
    'submission_atom_version_count',
    'get_atom_document',
    'refresh_audit_latest',
    'refresh_catalog_index'
)
AND pronamespace = 'public'::regnamespace
ORDER BY proname;

SELECT tgname, tgenabled
FROM pg_trigger
WHERE tgname IN (
    'on_auth_user_created',
    'trg_publishable_io_specs',
    'trg_publishable_parameters',
    'trg_publishable_descriptions',
    'trg_publishable_rollups',
    'trg_publishable_references',
    'trg_effective_tier_grants',
    'trg_effective_tier_roles',
    'trg_effective_tier_org_memberships',
    'trg_effective_tier_memberships'
)
ORDER BY tgname;

SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'users',
      'roles',
      'user_role_assignments',
      'organizations',
      'organization_email_domains',
      'organization_memberships',
      'user_memberships',
      'user_entitlement_grants',
      'contribution_events',
      'atom_source_repositories',
      'atoms',
      'atom_versions',
      'atom_authors',
      'hyperparams',
      'atom_benchmarks',
      'atom_io_specs',
      'atom_parameters',
      'atom_descriptions',
      'atom_references',
      'references_registry',
      'atom_audit_evidence',
      'atom_audit_rollups',
      'atom_uncertainty_estimates',
      'atom_verification_matches',
      'bounties',
      'submissions',
      'payouts',
      'verification_budgets',
      'verification_runs',
      'bounty_best_scores',
      'principal_targets',
      'execution_receipts',
      'dataset_splits',
      'settlement_payouts',
      'benchmark_suites',
      'benchmark_votes',
      'fuzz_results',
      'behavioral_equivalence_flags',
      'discipline_repos'
  )
ORDER BY tablename;

SELECT schemaname, tablename, policyname
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

SELECT COUNT(*) AS stale_publishable
FROM public.atoms
WHERE is_publishable IS DISTINCT FROM public.atom_is_publishable(atom_id);

SELECT COUNT(*) AS stale_effective_tier
FROM public.users
WHERE effective_tier IS DISTINCT FROM public.user_effective_entitlement(user_id);

SELECT COUNT(*) AS audit_latest_rows
FROM public.atom_audit_latest;

SELECT COUNT(*) AS catalog_index_rows
FROM public.catalog_atoms_index;
