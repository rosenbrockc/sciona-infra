-- Phase 2E: seed entitlement grants from contribution events and active roles.

INSERT INTO public.user_entitlement_grants
    (user_id, source_kind, entitlement_tier, source_ref, granted_at, expires_at)
SELECT
    ce.user_id,
    'contribution',
    'early_access',
    'backfill:contribution_events',
    ce.max_approved,
    ce.max_approved + INTERVAL '12 months'
FROM (
    SELECT user_id, MAX(approved_at) AS max_approved
    FROM public.contribution_events
    GROUP BY user_id
) ce
WHERE ce.max_approved + INTERVAL '12 months' > now()
AND NOT EXISTS (
    SELECT 1
    FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = ce.user_id
      AND ueg.source_kind = 'contribution'
      AND ueg.source_ref = 'backfill:contribution_events'
);

INSERT INTO public.user_entitlement_grants
    (user_id, source_kind, entitlement_tier, source_ref, granted_at)
SELECT
    ura.user_id,
    'role',
    r.grants_tier,
    'role:' || ura.role_name,
    ura.granted_at
FROM public.user_role_assignments ura
JOIN public.roles r ON r.role_name = ura.role_name
WHERE (ura.expires_at IS NULL OR ura.expires_at > now())
AND NOT EXISTS (
    SELECT 1
    FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = ura.user_id
      AND ueg.source_kind = 'role'
      AND ueg.source_ref = 'role:' || ura.role_name
);
