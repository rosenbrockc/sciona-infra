-- Phase 2E validation queries.

SELECT count(*) AS publishable_mismatches
FROM public.atoms a
WHERE a.is_publishable != public.atom_is_publishable(a.atom_id);

SELECT is_publishable, count(*) FROM public.atoms GROUP BY is_publishable;

SELECT event_kind, count(*) FROM public.contribution_events GROUP BY event_kind;

SELECT source_kind, count(*) FROM public.user_entitlement_grants GROUP BY source_kind;

SELECT effective_tier, count(*) FROM public.users GROUP BY effective_tier;

SELECT count(*) AS effective_tier_mismatches
FROM public.users u
WHERE u.effective_tier != public.user_effective_entitlement(u.user_id);
