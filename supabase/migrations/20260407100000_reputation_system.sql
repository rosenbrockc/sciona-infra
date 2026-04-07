-- Reputation points system for originators and architects.
-- Bounty earnings contribute ~25% of total; the rest rewards
-- publishing quality, adoption, citations, and community impact.

-- ============================================================
-- Helper: submission_contains_fqdn (already exists, no-op guard)
-- ============================================================

-- ============================================================
-- Originator Reputation Function
-- ============================================================

CREATE OR REPLACE FUNCTION public.compute_originator_reputation(p_user_id UUID)
RETURNS INTEGER
LANGUAGE sql STABLE
AS $$
WITH
-- 1. Publishing score: base points per approved atom + quality bonuses
author_atoms AS (
    SELECT aa.atom_id, a.fqdn, aa.contribution_share
    FROM public.atom_authors aa
    JOIN public.atoms a ON a.atom_id = aa.atom_id
    WHERE aa.user_id = p_user_id
      AND a.status = 'approved'
),
atom_quality AS (
    SELECT
        at.atom_id,
        -- Base 100 per atom (weighted by contribution share)
        100 AS base_pts,
        -- Smoke test bonus
        COALESCE((
            SELECT 20 FROM public.atom_audit_evidence e
            WHERE e.atom_id = at.atom_id AND e.audit_type = 'smoke_test' AND e.passed = TRUE
            LIMIT 1
        ), 0) AS smoke_pts,
        -- Fuzz test bonus
        COALESCE((
            SELECT 30 FROM public.atom_audit_evidence e
            WHERE e.atom_id = at.atom_id AND e.audit_type = 'fuzz_test' AND e.passed = TRUE
            LIMIT 1
        ), 0) AS fuzz_pts,
        -- Trusted audit verdict bonus
        COALESCE((
            SELECT 50 FROM public.atom_audit_rollups r
            WHERE r.atom_id = at.atom_id AND r.overall_verdict = 'trusted'
        ), 0) AS trusted_pts,
        -- Uncertainty estimates bonus
        COALESCE((
            SELECT 15 FROM public.atom_uncertainty_estimates ue
            WHERE ue.atom_id = at.atom_id
            LIMIT 1
        ), 0) AS uncertainty_pts,
        -- Verified references bonus (10 per ref, max 50)
        LEAST((
            SELECT COUNT(*) * 10 FROM public.atom_references r
            WHERE r.atom_id = at.atom_id AND r.verified = TRUE
        ), 50) AS ref_pts,
        -- Contribution share weight
        at.contribution_share
    FROM author_atoms at
),
publish_score AS (
    SELECT COALESCE(SUM(
        (base_pts + smoke_pts + fuzz_pts + trusted_pts + uncertainty_pts + ref_pts)
        * contribution_share
    ), 0)::INTEGER AS score
    FROM atom_quality
),

-- 2. Adoption score: atom usage in CDGs
adoption_winning AS (
    -- 40 pts per winning CDG that uses this user's atom
    SELECT COALESCE(SUM(40 * at.contribution_share), 0)::INTEGER AS score
    FROM author_atoms at
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, at.fqdn)
     AND s.is_winner = TRUE
    JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled'
),
adoption_any AS (
    -- 5 pts per any submission (non-winning) that uses this user's atom
    SELECT COALESCE(SUM(5 * at.contribution_share), 0)::INTEGER AS score
    FROM author_atoms at
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, at.fqdn)
     AND s.is_winner = FALSE
),
adoption_architects AS (
    -- 15 pts per distinct architect using this user's atoms
    SELECT (COUNT(DISTINCT s.architect_id) * 15)::INTEGER AS score
    FROM author_atoms at
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, at.fqdn)
    WHERE s.architect_id != p_user_id
),
adoption_domains AS (
    -- 25 pts per distinct domain_tag across the user's atoms that appear in winning CDGs
    SELECT (COUNT(DISTINCT dt) * 25)::INTEGER AS score
    FROM author_atoms at
    JOIN public.atoms a ON a.atom_id = at.atom_id
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, at.fqdn)
     AND s.is_winner = TRUE
    JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled',
    UNNEST(a.domain_tags) AS dt
    WHERE a.domain_tags IS NOT NULL AND array_length(a.domain_tags, 1) > 0
),

-- 3. Citation score: bibtex exports + version iterations
citation_bibtex AS (
    SELECT COALESCE((
        SELECT SUM(3) FROM public.bibtex_exports bx
        JOIN public.atoms a ON a.atom_id = bx.atom_id
        JOIN public.atom_authors aa ON aa.atom_id = a.atom_id AND aa.user_id = p_user_id
    ), 0)::INTEGER AS score
),
citation_versions AS (
    -- 20 pts per version after v1 (rewards maintenance)
    SELECT COALESCE(SUM(GREATEST(ver_count - 1, 0) * 20), 0)::INTEGER AS score
    FROM (
        SELECT at.atom_id, COUNT(av.version_id) AS ver_count
        FROM author_atoms at
        JOIN public.atom_versions av ON av.atom_id = at.atom_id
        GROUP BY at.atom_id
    ) sub
),

-- 4. Bounty score: 1 pt per $10 earned as originator
bounty_earnings AS (
    SELECT COALESCE(SUM(sp.amount) / 10, 0)::INTEGER AS score
    FROM public.settlement_payouts sp
    WHERE sp.recipient_id = p_user_id::text
      AND sp.role = 'originator'
),

-- 5. Community score: co-authors + referrals
community_coauthors AS (
    -- 25 pts per co-author (per atom)
    SELECT COALESCE(SUM(coauthor_count * 25), 0)::INTEGER AS score
    FROM (
        SELECT at.atom_id, COUNT(*) - 1 AS coauthor_count
        FROM author_atoms at
        JOIN public.atom_authors aa2 ON aa2.atom_id = at.atom_id
        GROUP BY at.atom_id
        HAVING COUNT(*) > 1
    ) sub
),
community_referrals AS (
    SELECT COALESCE((
        SELECT COUNT(*) * 50 FROM public.referrals r
        WHERE r.referrer_id = p_user_id
          AND r.value_created_at IS NOT NULL
    ), 0)::INTEGER AS score
)

SELECT
    (SELECT score FROM publish_score)
    + (SELECT score FROM adoption_winning)
    + (SELECT score FROM adoption_any)
    + (SELECT score FROM adoption_architects)
    + (SELECT score FROM adoption_domains)
    + (SELECT score FROM citation_bibtex)
    + (SELECT score FROM citation_versions)
    + (SELECT score FROM bounty_earnings)
    + (SELECT score FROM community_coauthors)
    + (SELECT score FROM community_referrals)
$$;


-- ============================================================
-- Architect Reputation Function
-- ============================================================

CREATE OR REPLACE FUNCTION public.compute_architect_reputation(p_user_id UUID)
RETURNS INTEGER
LANGUAGE sql STABLE
AS $$
WITH
-- 1. Activity score: submissions + verification passes
activity_submissions AS (
    SELECT
        (COUNT(*) * 20)::INTEGER AS base_score,
        (COUNT(*) FILTER (WHERE verification_status = 'public_verified') * 15)::INTEGER AS public_score,
        (COUNT(*) FILTER (WHERE verification_status = 'blind_verified') * 25)::INTEGER AS blind_score,
        (COUNT(*) FILTER (
            WHERE verified_metric_value IS NOT NULL
              AND claimed_metric_value > 0
              AND ABS(verified_metric_value - claimed_metric_value) / claimed_metric_value <= 0.05
        ) * 10)::INTEGER AS accuracy_score
    FROM public.submissions s
    WHERE s.architect_id = p_user_id
),

-- 2. Composition quality: diversity in winning CDGs
composition AS (
    SELECT COALESCE(SUM(atom_pts + author_pts + domain_pts), 0)::INTEGER AS score
    FROM (
        SELECT
            s.submission_id,
            -- Distinct atoms used: 20 pts each
            (SELECT COUNT(DISTINCT kv.key) * 20
             FROM jsonb_each_text(s.atom_versions) kv) AS atom_pts,
            -- Distinct authors represented: 15 pts each
            (SELECT COUNT(DISTINCT aa.user_id) * 15
             FROM jsonb_each_text(s.atom_versions) kv
             JOIN public.atoms a ON a.fqdn = kv.key
             JOIN public.atom_authors aa ON aa.atom_id = a.atom_id) AS author_pts,
            -- Distinct domain tags: 30 pts each
            (SELECT COUNT(DISTINCT dt) * 30
             FROM jsonb_each_text(s.atom_versions) kv
             JOIN public.atoms a ON a.fqdn = kv.key,
             UNNEST(a.domain_tags) AS dt) AS domain_pts
        FROM public.submissions s
        WHERE s.architect_id = p_user_id
          AND s.is_winner = TRUE
    ) sub
),

-- 3. Win score: base + win rate bonuses
win_base AS (
    SELECT
        (COUNT(*) FILTER (WHERE is_winner = TRUE) * 150)::INTEGER AS base_score,
        COUNT(*)::INTEGER AS total_subs,
        COUNT(*) FILTER (WHERE is_winner = TRUE)::INTEGER AS total_wins
    FROM public.submissions s
    WHERE s.architect_id = p_user_id
),
win_rate_bonus AS (
    SELECT
        CASE
            WHEN total_subs >= 5 AND total_wins::FLOAT / total_subs > 0.75 THEN 350  -- 100 + 250
            WHEN total_subs >= 3 AND total_wins::FLOAT / total_subs > 0.50 THEN 100
            ELSE 0
        END AS score
    FROM win_base
),

-- 4. Bounty earnings: 1 pt per $10
bounty_earnings AS (
    SELECT COALESCE(SUM(sp.amount) / 10, 0)::INTEGER AS score
    FROM public.settlement_payouts sp
    WHERE sp.recipient_id = p_user_id::text
      AND sp.role = 'architect'
),

-- 5. Community: referrals
community_referrals AS (
    SELECT COALESCE((
        SELECT COUNT(*) * 50 FROM public.referrals r
        WHERE r.referrer_id = p_user_id
          AND r.value_created_at IS NOT NULL
    ), 0)::INTEGER AS score
)

SELECT
    (SELECT base_score + public_score + blind_score + accuracy_score FROM activity_submissions)
    + (SELECT score FROM composition)
    + (SELECT base_score FROM win_base)
    + (SELECT score FROM win_rate_bonus)
    + (SELECT score FROM bounty_earnings)
    + (SELECT score FROM community_referrals)
$$;


-- ============================================================
-- Combined reputation function (picks the higher of the two)
-- ============================================================

CREATE OR REPLACE FUNCTION public.compute_reputation(p_user_id UUID)
RETURNS INTEGER
LANGUAGE sql STABLE
AS $$
    SELECT GREATEST(
        public.compute_originator_reputation(p_user_id),
        public.compute_architect_reputation(p_user_id)
    )
$$;


-- ============================================================
-- Materialized view: reputation_leaderboard
-- ============================================================

DROP MATERIALIZED VIEW IF EXISTS public.reputation_leaderboard;
CREATE MATERIALIZED VIEW public.reputation_leaderboard AS
WITH all_users AS (
    SELECT user_id, github_login
    FROM public.users
    WHERE NOT is_blacklisted
),
originator_scores AS (
    SELECT
        u.user_id,
        public.compute_originator_reputation(u.user_id) AS originator_rep
    FROM all_users u
),
architect_scores AS (
    SELECT
        u.user_id,
        public.compute_architect_reputation(u.user_id) AS architect_rep
    FROM all_users u
)
SELECT
    u.user_id,
    u.github_login,
    COALESCE(os.originator_rep, 0) AS originator_reputation,
    COALESCE(ars.architect_rep, 0) AS architect_reputation,
    GREATEST(COALESCE(os.originator_rep, 0), COALESCE(ars.architect_rep, 0)) AS total_reputation
FROM all_users u
LEFT JOIN originator_scores os ON os.user_id = u.user_id
LEFT JOIN architect_scores ars ON ars.user_id = u.user_id
WHERE COALESCE(os.originator_rep, 0) > 0
   OR COALESCE(ars.architect_rep, 0) > 0;

CREATE UNIQUE INDEX idx_reputation_leaderboard_user
    ON public.reputation_leaderboard (user_id);

CREATE INDEX idx_reputation_leaderboard_total
    ON public.reputation_leaderboard (total_reputation DESC);

GRANT SELECT ON public.reputation_leaderboard TO anon, authenticated;


-- ============================================================
-- Updated originator_impact view with reputation
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
    ast.atom_count,
    COALESCE(rl.originator_reputation, public.compute_originator_reputation(ast.originator_id)) AS reputation
FROM author_stats ast
JOIN public.users u ON u.user_id = ast.originator_id
LEFT JOIN bounty_stats bst
  ON bst.originator_id = ast.originator_id
LEFT JOIN public.reputation_leaderboard rl
  ON rl.user_id = ast.originator_id;


-- ============================================================
-- Updated architect_leaderboard view with reputation
-- ============================================================

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
    COALESCE(aa.distinct_atoms_used, 0) AS distinct_atoms_used,
    COALESCE(rl.architect_reputation, public.compute_architect_reputation(u.user_id)) AS reputation
FROM public.users u
JOIN architect_submissions asub ON asub.architect_id = u.user_id
LEFT JOIN architect_earnings ae ON ae.architect_id = u.user_id
LEFT JOIN architect_atoms aa ON aa.architect_id = u.user_id
LEFT JOIN public.reputation_leaderboard rl ON rl.user_id = u.user_id;

GRANT SELECT ON public.architect_leaderboard TO anon, authenticated;


-- ============================================================
-- RPC to refresh reputation (call after key events or on cron)
-- ============================================================

CREATE OR REPLACE FUNCTION public.refresh_reputation()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.reputation_leaderboard;

    -- Also update the cached reputation_score on users table
    UPDATE public.users u
    SET reputation_score = COALESCE(rl.total_reputation, 0),
        updated_at = now()
    FROM public.reputation_leaderboard rl
    WHERE rl.user_id = u.user_id
      AND u.reputation_score IS DISTINCT FROM rl.total_reputation;
END;
$$;


-- ============================================================
-- RPC to get a single user's live reputation breakdown
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_reputation_breakdown(p_user_id UUID)
RETURNS TABLE(
    originator_reputation INTEGER,
    architect_reputation INTEGER,
    total_reputation INTEGER
)
LANGUAGE sql STABLE
AS $$
    SELECT
        public.compute_originator_reputation(p_user_id),
        public.compute_architect_reputation(p_user_id),
        public.compute_reputation(p_user_id)
$$;


-- ============================================================
-- Detailed originator reputation breakdown (per-category scores)
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_originator_reputation_detail(p_user_id UUID)
RETURNS TABLE(
    category TEXT,
    score INTEGER,
    detail JSONB
)
LANGUAGE sql STABLE
AS $$
WITH
author_atoms AS (
    SELECT aa.atom_id, a.fqdn, a.status, aa.contribution_share
    FROM public.atom_authors aa
    JOIN public.atoms a ON a.atom_id = aa.atom_id
    WHERE aa.user_id = p_user_id
      AND a.status = 'approved'
),
-- Publishing detail
pub_atoms AS (
    SELECT
        at.atom_id,
        at.fqdn,
        at.contribution_share,
        100 AS base_pts,
        COALESCE((SELECT 20 FROM public.atom_audit_evidence e WHERE e.atom_id = at.atom_id AND e.audit_type = 'smoke_test' AND e.passed = TRUE LIMIT 1), 0) AS smoke_pts,
        COALESCE((SELECT 30 FROM public.atom_audit_evidence e WHERE e.atom_id = at.atom_id AND e.audit_type = 'fuzz_test' AND e.passed = TRUE LIMIT 1), 0) AS fuzz_pts,
        COALESCE((SELECT 50 FROM public.atom_audit_rollups r WHERE r.atom_id = at.atom_id AND r.overall_verdict = 'trusted'), 0) AS trusted_pts,
        COALESCE((SELECT 15 FROM public.atom_uncertainty_estimates ue WHERE ue.atom_id = at.atom_id LIMIT 1), 0) AS uncertainty_pts,
        LEAST((SELECT COUNT(*) * 10 FROM public.atom_references r WHERE r.atom_id = at.atom_id AND r.verified = TRUE), 50)::INTEGER AS ref_pts
    FROM author_atoms at
),
pub_score AS (
    SELECT
        COALESCE(SUM((base_pts + smoke_pts + fuzz_pts + trusted_pts + uncertainty_pts + ref_pts) * contribution_share), 0)::INTEGER AS score,
        jsonb_agg(jsonb_build_object(
            'fqdn', fqdn,
            'base', base_pts,
            'smoke', smoke_pts,
            'fuzz', fuzz_pts,
            'trusted', trusted_pts,
            'uncertainty', uncertainty_pts,
            'references', ref_pts,
            'share', contribution_share,
            'subtotal', ((base_pts + smoke_pts + fuzz_pts + trusted_pts + uncertainty_pts + ref_pts) * contribution_share)::INTEGER
        )) AS detail
    FROM pub_atoms
),
-- Adoption detail
adopt_winning_items AS (
    SELECT at.fqdn, b.title AS bounty_title, (40 * at.contribution_share)::INTEGER AS pts
    FROM author_atoms at
    JOIN public.submissions s ON public.submission_contains_fqdn(s.atom_versions, at.fqdn) AND s.is_winner = TRUE
    JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled'
),
adopt_any_items AS (
    SELECT at.fqdn, (5 * at.contribution_share)::INTEGER AS pts
    FROM author_atoms at
    JOIN public.submissions s ON public.submission_contains_fqdn(s.atom_versions, at.fqdn) AND s.is_winner = FALSE
),
adopt_architects AS (
    SELECT COUNT(DISTINCT s.architect_id) AS cnt
    FROM author_atoms at
    JOIN public.submissions s ON public.submission_contains_fqdn(s.atom_versions, at.fqdn)
    WHERE s.architect_id != p_user_id
),
adopt_domains AS (
    SELECT COUNT(DISTINCT dt) AS cnt
    FROM author_atoms at
    JOIN public.atoms a ON a.atom_id = at.atom_id
    JOIN public.submissions s ON public.submission_contains_fqdn(s.atom_versions, at.fqdn) AND s.is_winner = TRUE
    JOIN public.bounties b ON b.bounty_id = s.bounty_id AND b.status = 'settled',
    UNNEST(a.domain_tags) AS dt
    WHERE a.domain_tags IS NOT NULL AND array_length(a.domain_tags, 1) > 0
),
adopt_score AS (
    SELECT
        (COALESCE((SELECT SUM(pts) FROM adopt_winning_items), 0)
         + COALESCE((SELECT SUM(pts) FROM adopt_any_items), 0)
         + (SELECT cnt FROM adopt_architects) * 15
         + (SELECT cnt FROM adopt_domains) * 25
        )::INTEGER AS score,
        jsonb_build_object(
            'winning_cdg_uses', COALESCE((SELECT COUNT(*) FROM adopt_winning_items), 0),
            'winning_cdg_pts', COALESCE((SELECT SUM(pts) FROM adopt_winning_items), 0),
            'any_submission_uses', COALESCE((SELECT COUNT(*) FROM adopt_any_items), 0),
            'any_submission_pts', COALESCE((SELECT SUM(pts) FROM adopt_any_items), 0),
            'unique_architects', (SELECT cnt FROM adopt_architects),
            'architect_pts', (SELECT cnt FROM adopt_architects) * 15,
            'domain_reach', (SELECT cnt FROM adopt_domains),
            'domain_pts', (SELECT cnt FROM adopt_domains) * 25
        ) AS detail
),
-- Citation detail
cite_bibtex AS (
    SELECT COALESCE((
        SELECT COUNT(*) FROM public.bibtex_exports bx
        JOIN public.atoms a ON a.atom_id = bx.atom_id
        JOIN public.atom_authors aa ON aa.atom_id = a.atom_id AND aa.user_id = p_user_id
    ), 0)::INTEGER AS cnt
),
cite_versions AS (
    SELECT COALESCE(SUM(GREATEST(ver_count - 1, 0)), 0)::INTEGER AS cnt
    FROM (
        SELECT at.atom_id, COUNT(av.version_id) AS ver_count
        FROM author_atoms at
        JOIN public.atom_versions av ON av.atom_id = at.atom_id
        GROUP BY at.atom_id
    ) sub
),
cite_score AS (
    SELECT
        ((SELECT cnt FROM cite_bibtex) * 3 + (SELECT cnt FROM cite_versions) * 20)::INTEGER AS score,
        jsonb_build_object(
            'bibtex_exports', (SELECT cnt FROM cite_bibtex),
            'bibtex_pts', (SELECT cnt FROM cite_bibtex) * 3,
            'version_updates', (SELECT cnt FROM cite_versions),
            'version_pts', (SELECT cnt FROM cite_versions) * 20
        ) AS detail
),
-- Bounty earnings detail
earnings AS (
    SELECT
        COALESCE(SUM(sp.amount), 0) AS total_usd,
        COALESCE(SUM(sp.amount) / 10, 0)::INTEGER AS score
    FROM public.settlement_payouts sp
    WHERE sp.recipient_id = p_user_id::text AND sp.role = 'originator'
),
earnings_detail AS (
    SELECT
        (SELECT score FROM earnings) AS score,
        jsonb_build_object(
            'total_earned_usd', (SELECT total_usd FROM earnings),
            'points', (SELECT score FROM earnings)
        ) AS detail
),
-- Community detail
community_coauthors AS (
    SELECT COALESCE(SUM(coauthor_count), 0)::INTEGER AS cnt
    FROM (
        SELECT COUNT(*) - 1 AS coauthor_count
        FROM author_atoms at
        JOIN public.atom_authors aa2 ON aa2.atom_id = at.atom_id
        GROUP BY at.atom_id
        HAVING COUNT(*) > 1
    ) sub
),
community_referrals AS (
    SELECT COALESCE((SELECT COUNT(*) FROM public.referrals r WHERE r.referrer_id = p_user_id AND r.value_created_at IS NOT NULL), 0)::INTEGER AS cnt
),
community_score AS (
    SELECT
        ((SELECT cnt FROM community_coauthors) * 25 + (SELECT cnt FROM community_referrals) * 50)::INTEGER AS score,
        jsonb_build_object(
            'coauthor_count', (SELECT cnt FROM community_coauthors),
            'coauthor_pts', (SELECT cnt FROM community_coauthors) * 25,
            'activated_referrals', (SELECT cnt FROM community_referrals),
            'referral_pts', (SELECT cnt FROM community_referrals) * 50
        ) AS detail
)

SELECT 'publishing'::TEXT, (SELECT score FROM pub_score), COALESCE((SELECT detail FROM pub_score), '[]'::jsonb)
UNION ALL
SELECT 'adoption'::TEXT, (SELECT score FROM adopt_score), (SELECT detail FROM adopt_score)
UNION ALL
SELECT 'citation'::TEXT, (SELECT score FROM cite_score), (SELECT detail FROM cite_score)
UNION ALL
SELECT 'bounty_earnings'::TEXT, (SELECT score FROM earnings_detail), (SELECT detail FROM earnings_detail)
UNION ALL
SELECT 'community'::TEXT, (SELECT score FROM community_score), (SELECT detail FROM community_score)
$$;


-- ============================================================
-- Detailed architect reputation breakdown (per-category scores)
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_architect_reputation_detail(p_user_id UUID)
RETURNS TABLE(
    category TEXT,
    score INTEGER,
    detail JSONB
)
LANGUAGE sql STABLE
AS $$
WITH
-- Activity detail
activity AS (
    SELECT
        COUNT(*)::INTEGER AS total_subs,
        (COUNT(*) * 20)::INTEGER AS base_score,
        COUNT(*) FILTER (WHERE verification_status = 'public_verified')::INTEGER AS public_count,
        (COUNT(*) FILTER (WHERE verification_status = 'public_verified') * 15)::INTEGER AS public_score,
        COUNT(*) FILTER (WHERE verification_status = 'blind_verified')::INTEGER AS blind_count,
        (COUNT(*) FILTER (WHERE verification_status = 'blind_verified') * 25)::INTEGER AS blind_score,
        COUNT(*) FILTER (
            WHERE verified_metric_value IS NOT NULL
              AND claimed_metric_value > 0
              AND ABS(verified_metric_value - claimed_metric_value) / claimed_metric_value <= 0.05
        )::INTEGER AS accurate_count,
        (COUNT(*) FILTER (
            WHERE verified_metric_value IS NOT NULL
              AND claimed_metric_value > 0
              AND ABS(verified_metric_value - claimed_metric_value) / claimed_metric_value <= 0.05
        ) * 10)::INTEGER AS accuracy_score
    FROM public.submissions s WHERE s.architect_id = p_user_id
),
activity_result AS (
    SELECT
        (base_score + public_score + blind_score + accuracy_score)::INTEGER AS score,
        jsonb_build_object(
            'submissions', total_subs,
            'submission_pts', base_score,
            'public_verified', public_count,
            'public_pts', public_score,
            'blind_verified', blind_count,
            'blind_pts', blind_score,
            'accurate_claims', accurate_count,
            'accuracy_pts', accuracy_score
        ) AS detail
    FROM activity
),
-- Composition detail
comp_cdgs AS (
    SELECT
        s.submission_id,
        (SELECT COUNT(DISTINCT kv.key) FROM jsonb_each_text(s.atom_versions) kv)::INTEGER AS atom_cnt,
        (SELECT COUNT(DISTINCT aa.user_id)
         FROM jsonb_each_text(s.atom_versions) kv
         JOIN public.atoms a ON a.fqdn = kv.key
         JOIN public.atom_authors aa ON aa.atom_id = a.atom_id)::INTEGER AS author_cnt,
        (SELECT COUNT(DISTINCT dt)
         FROM jsonb_each_text(s.atom_versions) kv
         JOIN public.atoms a ON a.fqdn = kv.key,
         UNNEST(a.domain_tags) AS dt)::INTEGER AS domain_cnt
    FROM public.submissions s
    WHERE s.architect_id = p_user_id AND s.is_winner = TRUE
),
comp_result AS (
    SELECT
        COALESCE(SUM(atom_cnt * 20 + author_cnt * 15 + domain_cnt * 30), 0)::INTEGER AS score,
        jsonb_build_object(
            'winning_cdgs', COALESCE((SELECT COUNT(*) FROM comp_cdgs), 0),
            'total_atoms_pts', COALESCE(SUM(atom_cnt * 20), 0),
            'total_authors_pts', COALESCE(SUM(author_cnt * 15), 0),
            'total_domains_pts', COALESCE(SUM(domain_cnt * 30), 0)
        ) AS detail
    FROM comp_cdgs
),
-- Win detail
wins AS (
    SELECT
        COUNT(*) FILTER (WHERE is_winner = TRUE)::INTEGER AS win_count,
        COUNT(*)::INTEGER AS total_subs,
        (COUNT(*) FILTER (WHERE is_winner = TRUE) * 150)::INTEGER AS base_score
    FROM public.submissions s WHERE s.architect_id = p_user_id
),
win_rate_bonus AS (
    SELECT CASE
        WHEN total_subs >= 5 AND win_count::FLOAT / total_subs > 0.75 THEN 350
        WHEN total_subs >= 3 AND win_count::FLOAT / total_subs > 0.50 THEN 100
        ELSE 0
    END AS bonus
    FROM wins
),
win_result AS (
    SELECT
        ((SELECT base_score FROM wins) + (SELECT bonus FROM win_rate_bonus))::INTEGER AS score,
        jsonb_build_object(
            'wins', (SELECT win_count FROM wins),
            'total_submissions', (SELECT total_subs FROM wins),
            'win_pts', (SELECT base_score FROM wins),
            'win_rate', CASE WHEN (SELECT total_subs FROM wins) > 0
                THEN ROUND((SELECT win_count FROM wins)::NUMERIC / (SELECT total_subs FROM wins), 2)
                ELSE 0 END,
            'win_rate_bonus', (SELECT bonus FROM win_rate_bonus)
        ) AS detail
),
-- Bounty earnings
earnings AS (
    SELECT
        COALESCE(SUM(sp.amount), 0) AS total_usd,
        COALESCE(SUM(sp.amount) / 10, 0)::INTEGER AS score
    FROM public.settlement_payouts sp
    WHERE sp.recipient_id = p_user_id::text AND sp.role = 'architect'
),
earnings_result AS (
    SELECT
        (SELECT score FROM earnings) AS score,
        jsonb_build_object(
            'total_earned_usd', (SELECT total_usd FROM earnings),
            'points', (SELECT score FROM earnings)
        ) AS detail
),
-- Community
community_referrals AS (
    SELECT COALESCE((SELECT COUNT(*) FROM public.referrals r WHERE r.referrer_id = p_user_id AND r.value_created_at IS NOT NULL), 0)::INTEGER AS cnt
),
community_result AS (
    SELECT
        ((SELECT cnt FROM community_referrals) * 50)::INTEGER AS score,
        jsonb_build_object(
            'activated_referrals', (SELECT cnt FROM community_referrals),
            'referral_pts', (SELECT cnt FROM community_referrals) * 50
        ) AS detail
)

SELECT 'activity'::TEXT, (SELECT score FROM activity_result), (SELECT detail FROM activity_result)
UNION ALL
SELECT 'composition'::TEXT, (SELECT score FROM comp_result), (SELECT detail FROM comp_result)
UNION ALL
SELECT 'wins'::TEXT, (SELECT score FROM win_result), (SELECT detail FROM win_result)
UNION ALL
SELECT 'bounty_earnings'::TEXT, (SELECT score FROM earnings_result), (SELECT detail FROM earnings_result)
UNION ALL
SELECT 'community'::TEXT, (SELECT score FROM community_result), (SELECT detail FROM community_result)
$$;
