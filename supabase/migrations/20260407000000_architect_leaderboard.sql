-- Architect leaderboard view: ranks architects by bounty wins and total earnings
-- Mirrors originator_impact but from the architect (CDG submitter) perspective.

DROP VIEW IF EXISTS public.architect_leaderboard CASCADE;
CREATE VIEW public.architect_leaderboard
WITH (security_invoker = true)
AS
WITH architect_submissions AS (
    SELECT
        s.architect_id,
        COUNT(*) AS submission_count,
        COUNT(*) FILTER (WHERE s.is_winner = TRUE) AS win_count
    FROM public.submissions s
    GROUP BY s.architect_id
),
architect_earnings AS (
    SELECT
        sp.recipient_id::uuid AS architect_id,
        COALESCE(SUM(sp.amount), 0) AS total_earned,
        COUNT(DISTINCT sp.bounty_id) AS bounties_won
    FROM public.settlement_payouts sp
    WHERE sp.role = 'architect'
    GROUP BY sp.recipient_id
),
architect_atoms AS (
    -- Count distinct atoms used across all winning CDGs
    SELECT
        s.architect_id,
        COUNT(DISTINCT kv.value) AS distinct_atoms_used
    FROM public.submissions s,
         jsonb_each_text(s.atom_versions) AS kv
    WHERE s.is_winner = TRUE
    GROUP BY s.architect_id
)
SELECT
    u.user_id AS architect_id,
    u.github_login,
    COALESCE(asub.submission_count, 0) AS submission_count,
    COALESCE(asub.win_count, 0) AS win_count,
    COALESCE(ae.total_earned, 0) AS total_earned,
    COALESCE(ae.bounties_won, 0) AS bounties_won,
    COALESCE(aa.distinct_atoms_used, 0) AS distinct_atoms_used
FROM public.users u
JOIN architect_submissions asub ON asub.architect_id = u.user_id
LEFT JOIN architect_earnings ae ON ae.architect_id = u.user_id
LEFT JOIN architect_atoms aa ON aa.architect_id = u.user_id;

-- Allow public read access
GRANT SELECT ON public.architect_leaderboard TO anon, authenticated;
