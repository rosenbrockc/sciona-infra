-- Badge system and referral tracking
-- Adds gamification layer: 15 badges (+ 2 hidden) across 4 tracks,
-- 3 tiers each, plus referral codes and value-gated referral tracking.

-- ============================================================
-- Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS badge_definitions (
    badge_id     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    track        TEXT NOT NULL CHECK (track IN ('originator','architect','vanguard','evangelist')),
    icon_slug    TEXT NOT NULL DEFAULT '',
    is_hidden    BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order   INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS badge_milestones (
    milestone_id    TEXT PRIMARY KEY,
    badge_id        TEXT NOT NULL REFERENCES badge_definitions(badge_id),
    tier            TEXT NOT NULL CHECK (tier IN ('node','edge','lattice','single')),
    threshold_value NUMERIC NOT NULL,
    threshold_unit  TEXT NOT NULL DEFAULT 'count'
);

CREATE TABLE IF NOT EXISTS user_badges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(user_id),
    milestone_id TEXT NOT NULL REFERENCES badge_milestones(milestone_id),
    awarded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    progress_value NUMERIC NOT NULL DEFAULT 0,
    UNIQUE (user_id, milestone_id)
);

CREATE TABLE IF NOT EXISTS badge_progress (
    user_id              UUID NOT NULL REFERENCES users(user_id),
    badge_id             TEXT NOT NULL REFERENCES badge_definitions(badge_id),
    current_value        NUMERIC NOT NULL DEFAULT 0,
    highest_awarded_tier TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, badge_id)
);

CREATE TABLE IF NOT EXISTS referral_codes (
    code        TEXT PRIMARY KEY,
    referrer_id UUID NOT NULL REFERENCES users(user_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    max_uses    INT NOT NULL DEFAULT 50,
    use_count   INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS referrals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id     UUID NOT NULL REFERENCES users(user_id),
    referee_id      UUID NOT NULL REFERENCES users(user_id),
    code            TEXT NOT NULL REFERENCES referral_codes(code),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_value_event TEXT,
    value_created_at  TIMESTAMPTZ,
    UNIQUE (referee_id)
);

CREATE TABLE IF NOT EXISTS bibtex_exports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id     UUID NOT NULL REFERENCES atoms(atom_id),
    exported_by UUID NOT NULL REFERENCES users(user_id),
    ip_hash     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_badges_user   ON user_badges(user_id);
CREATE INDEX IF NOT EXISTS idx_badge_progress_user ON badge_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer  ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_codes_referrer ON referral_codes(referrer_id);
CREATE INDEX IF NOT EXISTS idx_bibtex_exports_atom ON bibtex_exports(atom_id);

-- ============================================================
-- RLS Policies
-- ============================================================

ALTER TABLE badge_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE badge_milestones  ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_badges       ENABLE ROW LEVEL SECURITY;
ALTER TABLE badge_progress    ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_codes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals         ENABLE ROW LEVEL SECURITY;
ALTER TABLE bibtex_exports    ENABLE ROW LEVEL SECURITY;

-- Public read for badge catalog
CREATE POLICY badge_definitions_select ON badge_definitions FOR SELECT USING (true);
CREATE POLICY badge_milestones_select  ON badge_milestones  FOR SELECT USING (true);
CREATE POLICY user_badges_select       ON user_badges       FOR SELECT USING (true);

-- Own-user only for progress
CREATE POLICY badge_progress_select ON badge_progress
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY badge_progress_all ON badge_progress
    FOR ALL USING (auth.uid() = user_id);

-- Own-user for referral data
CREATE POLICY referral_codes_select ON referral_codes
    FOR SELECT USING (auth.uid() = referrer_id);
CREATE POLICY referral_codes_insert ON referral_codes
    FOR INSERT WITH CHECK (auth.uid() = referrer_id);

CREATE POLICY referrals_select_referrer ON referrals
    FOR SELECT USING (auth.uid() = referrer_id);
CREATE POLICY referrals_select_referee ON referrals
    FOR SELECT USING (auth.uid() = referee_id);

-- Service role can do anything (badges awarded server-side)
CREATE POLICY user_badges_service ON user_badges
    FOR ALL USING (current_setting('role') = 'service_role');
CREATE POLICY badge_progress_service ON badge_progress
    FOR ALL USING (current_setting('role') = 'service_role');
CREATE POLICY referrals_service ON referrals
    FOR ALL USING (current_setting('role') = 'service_role');
CREATE POLICY referral_codes_service ON referral_codes
    FOR ALL USING (current_setting('role') = 'service_role');
CREATE POLICY bibtex_exports_service ON bibtex_exports
    FOR ALL USING (current_setting('role') = 'service_role');

-- ============================================================
-- Badge progress SQL functions
-- ============================================================

-- Keystone: count CDGs that use this user's atoms as dependencies
CREATE OR REPLACE FUNCTION badge_keystone_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(DISTINCT s.submission_id)::NUMERIC
    FROM submissions s
    JOIN atoms a ON a.owner_id != p_user_id
    JOIN atom_versions av ON av.atom_id = a.atom_id
    WHERE s.is_winner = TRUE
      AND s.atom_versions::jsonb ? a.fqdn
      AND EXISTS (
          SELECT 1 FROM atoms my_a
          JOIN atom_versions my_av ON my_av.atom_id = my_a.atom_id
          WHERE my_a.owner_id = p_user_id
            AND s.atom_versions::jsonb ? my_a.fqdn
      );
$$;

-- Sovereign: total originator payouts
CREATE OR REPLACE FUNCTION badge_sovereign_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(amount), 0)
    FROM settlement_payouts
    WHERE recipient_id = p_user_id::text
      AND role = 'originator';
$$;

-- Laureate: bibtex export count for user's atoms
CREATE OR REPLACE FUNCTION badge_laureate_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM bibtex_exports be
    JOIN atoms a ON a.atom_id = be.atom_id
    WHERE a.owner_id = p_user_id;
$$;

-- Anvil: max inputs tested in passing fuzz results for user's atoms
CREATE OR REPLACE FUNCTION badge_anvil_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT COALESCE(MAX(fr.inputs_tested), 0)::NUMERIC
    FROM fuzz_results fr
    JOIN atoms a ON a.fqdn = fr.atom_fqdn
    WHERE a.owner_id = p_user_id
      AND fr.passed = TRUE;
$$;

-- Dead-End Breaker: count winning submissions
CREATE OR REPLACE FUNCTION badge_deadend_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM submissions
    WHERE architect_id = p_user_id
      AND is_winner = TRUE;
$$;

-- Synthesizer: max distinct atom authors in any single winning CDG
CREATE OR REPLACE FUNCTION badge_synthesizer_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT COALESCE(MAX(author_count), 0)::NUMERIC
    FROM (
        SELECT s.submission_id,
               count(DISTINCT aa.user_id) AS author_count
        FROM submissions s
        JOIN atoms a ON s.atom_versions::jsonb ? a.fqdn
        JOIN atom_authors aa ON aa.atom_id = a.atom_id
        WHERE s.architect_id = p_user_id
          AND s.is_winner = TRUE
        GROUP BY s.submission_id
    ) sub;
$$;

-- Polymath: max distinct domain tags in any single winning CDG
CREATE OR REPLACE FUNCTION badge_polymath_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT COALESCE(MAX(tag_count), 0)::NUMERIC
    FROM (
        SELECT s.submission_id,
               count(DISTINCT tag) AS tag_count
        FROM submissions s
        JOIN atoms a ON s.atom_versions::jsonb ? a.fqdn,
        LATERAL unnest(a.domain_tags) AS tag
        WHERE s.architect_id = p_user_id
          AND s.is_winner = TRUE
        GROUP BY s.submission_id
    ) sub;
$$;

-- Chain Reaction: count referrals that created value
CREATE OR REPLACE FUNCTION badge_chain_reaction_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM referrals
    WHERE referrer_id = p_user_id
      AND value_created_at IS NOT NULL;
$$;

-- Rainmaker: 1 if any referee funded a bounty > $500
CREATE OR REPLACE FUNCTION badge_rainmaker_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM referrals r
        JOIN bounties b ON b.principal_id = r.referee_id
        WHERE r.referrer_id = p_user_id
          AND b.escrow_amount > 500
          AND b.status NOT IN ('draft','cancelled')
    ) THEN 1 ELSE 0 END::NUMERIC;
$$;

-- Lab Director: count referrals from same email domain as referrer
CREATE OR REPLACE FUNCTION badge_lab_director_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM referrals r
    JOIN users u_referrer ON u_referrer.user_id = r.referrer_id
    JOIN users u_referee  ON u_referee.user_id  = r.referee_id
    WHERE r.referrer_id = p_user_id
      AND r.value_created_at IS NOT NULL
      AND u_referrer.email LIKE '%@%'
      AND split_part(u_referrer.email, '@', 2) = split_part(u_referee.email, '@', 2);
$$;

-- Atom count for Prolific badge
CREATE OR REPLACE FUNCTION badge_prolific_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM atoms
    WHERE owner_id = p_user_id
      AND status = 'approved';
$$;

-- Total bounties won value for Titan badge
CREATE OR REPLACE FUNCTION badge_titan_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(b.escrow_amount), 0)::NUMERIC
    FROM submissions s
    JOIN bounties b ON b.bounty_id = s.bounty_id
    WHERE s.architect_id = p_user_id
      AND s.is_winner = TRUE;
$$;

-- Graverobber: won a bounty that was previously expired/cancelled and reposted
CREATE OR REPLACE FUNCTION badge_graverobber_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(*)::NUMERIC
    FROM submissions s
    JOIN bounties b ON b.bounty_id = s.bounty_id
    WHERE s.architect_id = p_user_id
      AND s.is_winner = TRUE
      AND b.reposted_from IS NOT NULL;
$$;

-- Frankenstein: won with a CDG using atoms that previously had failing fuzz results
CREATE OR REPLACE FUNCTION badge_frankenstein_progress(p_user_id UUID)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT count(DISTINCT s.submission_id)::NUMERIC
    FROM submissions s
    JOIN atoms a ON s.atom_versions::jsonb ? a.fqdn
    JOIN fuzz_results fr ON fr.atom_fqdn = a.fqdn AND fr.passed = FALSE
    WHERE s.architect_id = p_user_id
      AND s.is_winner = TRUE;
$$;

-- Rarity percentile: % of users who DON'T have a given milestone
CREATE OR REPLACE FUNCTION badge_rarity_percentile(p_milestone_id TEXT)
RETURNS NUMERIC LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN (SELECT count(*) FROM users) = 0 THEN 100
        ELSE (
            100.0 - (
                (SELECT count(DISTINCT user_id)::NUMERIC FROM user_badges WHERE milestone_id = p_milestone_id)
                / (SELECT count(*)::NUMERIC FROM users)
                * 100
            )
        )
    END;
$$;

-- ============================================================
-- Seed data: Badge definitions
-- ============================================================

INSERT INTO badge_definitions (badge_id, display_name, description, track, icon_slug, is_hidden, sort_order) VALUES
-- Originator track
('prolific',      'Prolific',        'Publish atoms to the registry',                          'originator', 'prolific',      FALSE, 1),
('keystone',      'Keystone',        'Your atoms are used as dependencies in winning CDGs',    'originator', 'keystone',      FALSE, 2),
('sovereign',     'Sovereign',       'Earn originator royalties from settlement payouts',      'originator', 'sovereign',     FALSE, 3),
('laureate',      'Laureate',        'Your atoms are cited via BibTeX exports',                'originator', 'laureate',      FALSE, 4),
-- Architect track
('deadend',       'Dead-End Breaker','Win bounties with verified submissions',                 'architect',  'deadend',       FALSE, 5),
('titan',         'Titan',           'Accumulate bounty winnings',                             'architect',  'titan',         FALSE, 6),
('synthesizer',   'Synthesizer',     'Compose CDGs from many authors\'' || ' atoms',           'architect',  'synthesizer',   FALSE, 7),
('polymath',      'Polymath',        'Compose CDGs spanning many domains',                     'architect',  'polymath',      FALSE, 8),
-- Vanguard track
('anvil',         'Anvil',           'Stress-test atoms with fuzz testing',                    'vanguard',   'anvil',         FALSE, 9),
-- Evangelist track
('chain_reaction','Chain Reaction',  'Refer users who create real value',                      'evangelist', 'chain_reaction',FALSE, 10),
('rainmaker',     'Rainmaker',       'Refer someone who funds a bounty over $500',             'evangelist', 'rainmaker',     FALSE, 11),
('lab_director',  'Lab Director',    'Bring colleagues from your organization',                'evangelist', 'lab_director',  FALSE, 12),
-- Hidden badges
('graverobber',   'Graverobber',     'Win a reposted bounty that others abandoned',            'architect',  'graverobber',   TRUE,  13),
('frankenstein',  'Frankenstein',    'Win using atoms that previously failed fuzz testing',     'architect',  'frankenstein',  TRUE,  14)
ON CONFLICT (badge_id) DO NOTHING;

-- ============================================================
-- Seed data: Badge milestones (~45 rows)
-- ============================================================

INSERT INTO badge_milestones (milestone_id, badge_id, tier, threshold_value, threshold_unit) VALUES
-- Prolific
('prolific_node',    'prolific',    'node',    1,   'atoms'),
('prolific_edge',    'prolific',    'edge',    10,  'atoms'),
('prolific_lattice', 'prolific',    'lattice', 50,  'atoms'),
-- Keystone
('keystone_node',    'keystone',    'node',    1,   'cdgs'),
('keystone_edge',    'keystone',    'edge',    10,  'cdgs'),
('keystone_lattice', 'keystone',    'lattice', 50,  'cdgs'),
-- Sovereign
('sovereign_node',    'sovereign',  'node',    100,    'usd'),
('sovereign_edge',    'sovereign',  'edge',    5000,   'usd'),
('sovereign_lattice', 'sovereign',  'lattice', 50000,  'usd'),
-- Laureate
('laureate_node',    'laureate',    'node',    5,   'exports'),
('laureate_edge',    'laureate',    'edge',    50,  'exports'),
('laureate_lattice', 'laureate',    'lattice', 500, 'exports'),
-- Dead-End Breaker
('deadend_node',     'deadend',     'node',    1,   'wins'),
('deadend_edge',     'deadend',     'edge',    5,   'wins'),
('deadend_lattice',  'deadend',     'lattice', 25,  'wins'),
-- Titan
('titan_node',       'titan',       'node',    1000,    'usd'),
('titan_edge',       'titan',       'edge',    25000,   'usd'),
('titan_lattice',    'titan',       'lattice', 100000,  'usd'),
-- Synthesizer
('synthesizer_node',    'synthesizer', 'node',    3,  'authors'),
('synthesizer_edge',    'synthesizer', 'edge',    10, 'authors'),
('synthesizer_lattice', 'synthesizer', 'lattice', 25, 'authors'),
-- Polymath
('polymath_node',    'polymath',    'node',    3,   'domains'),
('polymath_edge',    'polymath',    'edge',    7,   'domains'),
('polymath_lattice', 'polymath',    'lattice', 15,  'domains'),
-- Anvil
('anvil_node',       'anvil',       'node',    100,   'inputs'),
('anvil_edge',       'anvil',       'edge',    1000,  'inputs'),
('anvil_lattice',    'anvil',       'lattice', 10000, 'inputs'),
-- Chain Reaction
('chain_reaction_node',    'chain_reaction', 'node',    1,  'referrals'),
('chain_reaction_edge',    'chain_reaction', 'edge',    5,  'referrals'),
('chain_reaction_lattice', 'chain_reaction', 'lattice', 25, 'referrals'),
-- Rainmaker
('rainmaker_single', 'rainmaker',   'single',  1,   'boolean'),
-- Lab Director
('lab_director_node',    'lab_director', 'node',    3,  'colleagues'),
('lab_director_edge',    'lab_director', 'edge',    10, 'colleagues'),
('lab_director_lattice', 'lab_director', 'lattice', 25, 'colleagues'),
-- Hidden: Graverobber
('graverobber_single', 'graverobber', 'single', 1, 'wins'),
-- Hidden: Frankenstein
('frankenstein_single', 'frankenstein', 'single', 1, 'wins')
ON CONFLICT (milestone_id) DO NOTHING;
