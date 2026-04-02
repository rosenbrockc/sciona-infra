-- Phase 5: Enterprise Authentik SSO + SCIM support.

-- Allow enterprise users without GitHub accounts.
ALTER TABLE public.users ALTER COLUMN github_id DROP NOT NULL;
ALTER TABLE public.users ALTER COLUMN github_id SET DEFAULT 0;

-- The legacy UNIQUE constraint blocks multiple enterprise users when a
-- placeholder github_id is used. Replace it with a partial unique index that
-- only applies to real GitHub IDs.
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_github_id_key;
DROP INDEX IF EXISTS idx_users_github_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github_id_unique
    ON public.users (github_id)
    WHERE github_id IS NOT NULL AND github_id != 0;

-- Enterprise identity columns.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS oidc_sub TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS oidc_issuer TEXT NOT NULL DEFAULT '';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS org_slug TEXT NOT NULL DEFAULT '';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'github'
    CHECK (auth_provider IN ('github', 'oidc'));
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS scim_external_id TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS scim_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oidc_sub
    ON public.users (oidc_sub);

CREATE INDEX IF NOT EXISTS idx_users_org_slug
    ON public.users (org_slug);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_scim_external_id
    ON public.users (scim_external_id);
