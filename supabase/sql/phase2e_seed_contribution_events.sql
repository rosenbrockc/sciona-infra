-- ============================================================
-- Phase 2E: seed contribution_events from existing relational data
-- Safe for reruns because contribution_events has
-- UNIQUE (user_id, event_kind, entity_id).
-- ============================================================

BEGIN;

INSERT INTO public.contribution_events
    (user_id, event_kind, entity_kind, entity_id, entity_fqdn, approved_at, source, source_ref)
SELECT
    aa.user_id,
    'atom_authorship',
    'atom',
    aa.atom_id,
    a.fqdn,
    a.created_at,
    'backfill',
    ''
FROM public.atom_authors aa
JOIN public.atoms a ON a.atom_id = aa.atom_id
WHERE a.status = 'approved'
ON CONFLICT DO NOTHING;

INSERT INTO public.contribution_events
    (user_id, event_kind, entity_kind, entity_id, entity_fqdn, approved_at, source, source_ref)
SELECT
    s.architect_id,
    'bounty_win',
    'bounty',
    s.bounty_id,
    b.title,
    COALESCE(s.verified_at, s.submitted_at),
    'backfill',
    s.submission_id::text
FROM public.submissions s
JOIN public.bounties b ON b.bounty_id = s.bounty_id
WHERE s.is_winner = TRUE
  AND b.status IN ('verified', 'settled')
ON CONFLICT DO NOTHING;

COMMIT;
