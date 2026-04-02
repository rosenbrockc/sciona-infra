SET session_replication_role = 'replica';

TRUNCATE public.discipline_repos CASCADE;
TRUNCATE public.behavioral_equivalence_flags CASCADE;
TRUNCATE public.fuzz_results CASCADE;
TRUNCATE public.benchmark_votes CASCADE;
TRUNCATE public.benchmark_suites CASCADE;

TRUNCATE public.settlement_payouts CASCADE;
TRUNCATE public.dataset_splits CASCADE;
TRUNCATE public.execution_receipts CASCADE;
TRUNCATE public.principal_targets CASCADE;
TRUNCATE public.bounty_best_scores CASCADE;
TRUNCATE public.verification_runs CASCADE;
TRUNCATE public.verification_budgets CASCADE;

TRUNCATE public.payouts CASCADE;
TRUNCATE public.submissions CASCADE;
TRUNCATE public.bounties CASCADE;

TRUNCATE public.atom_benchmarks CASCADE;
TRUNCATE public.hyperparams CASCADE;
TRUNCATE public.atom_authors CASCADE;
TRUNCATE public.atom_versions CASCADE;
TRUNCATE public.atoms CASCADE;

TRUNCATE public.organization_memberships CASCADE;
TRUNCATE public.organization_email_domains CASCADE;
TRUNCATE public.organizations CASCADE;

TRUNCATE public.users CASCADE;
DELETE FROM auth.users;

SET session_replication_role = 'origin';
