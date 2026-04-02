-- Phase 2E: recompute users.effective_tier after seeding grants.

ALTER TABLE public.user_entitlement_grants DISABLE TRIGGER trg_effective_tier_grants;
ALTER TABLE public.user_role_assignments DISABLE TRIGGER trg_effective_tier_roles;
ALTER TABLE public.organization_memberships DISABLE TRIGGER trg_effective_tier_org_memberships;
ALTER TABLE public.user_memberships DISABLE TRIGGER trg_effective_tier_memberships;

UPDATE public.users
   SET effective_tier = public.user_effective_entitlement(user_id),
       updated_at = now();

ALTER TABLE public.user_entitlement_grants ENABLE TRIGGER trg_effective_tier_grants;
ALTER TABLE public.user_role_assignments ENABLE TRIGGER trg_effective_tier_roles;
ALTER TABLE public.organization_memberships ENABLE TRIGGER trg_effective_tier_org_memberships;
ALTER TABLE public.user_memberships ENABLE TRIGGER trg_effective_tier_memberships;
