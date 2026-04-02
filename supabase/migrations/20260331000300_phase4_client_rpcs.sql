-- Phase 4 RPCs for Supabase-compatible API handlers.

CREATE OR REPLACE FUNCTION public.get_originator_impact(p_user_id UUID)
RETURNS TABLE (
    originator_id UUID,
    github_login TEXT,
    bounty_count BIGINT,
    total_bounty_value NUMERIC,
    atom_count BIGINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH author_atoms AS (
        SELECT aa.user_id AS originator_id, aa.atom_id, a.fqdn
        FROM public.atom_authors aa
        JOIN public.atoms a ON a.atom_id = aa.atom_id
        WHERE aa.user_id = p_user_id
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
        ast.atom_count
    FROM author_stats ast
    JOIN public.users u ON u.user_id = ast.originator_id
    LEFT JOIN bounty_stats bst
      ON bst.originator_id = ast.originator_id;
$$;

CREATE OR REPLACE FUNCTION public.get_originator_bounty_values(p_user_id UUID)
RETURNS TABLE (
    bounty_id UUID,
    escrow_amount NUMERIC
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH author_atoms AS (
        SELECT aa.user_id AS originator_id, a.fqdn
        FROM public.atom_authors aa
        JOIN public.atoms a ON a.atom_id = aa.atom_id
        WHERE aa.user_id = p_user_id
    )
    SELECT DISTINCT
        b.bounty_id,
        b.escrow_amount
    FROM author_atoms aa
    JOIN public.submissions s
      ON public.submission_contains_fqdn(s.atom_versions, aa.fqdn)
     AND s.is_winner = TRUE
    JOIN public.bounties b
      ON b.bounty_id = s.bounty_id
     AND b.status = 'settled'
    ORDER BY b.escrow_amount DESC, b.bounty_id;
$$;

CREATE OR REPLACE FUNCTION public.get_bounty_leaderboard(
    p_bounty_id UUID,
    p_limit INTEGER DEFAULT 50,
    p_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    submission_id UUID,
    architect_id UUID,
    metric_values JSONB,
    verified_at TIMESTAMPTZ,
    total_count BIGINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH ranked AS (
        SELECT
            vr.submission_id,
            s.architect_id,
            vr.metric_values,
            vr.completed_at AS verified_at,
            COUNT(*) OVER () AS total_count
        FROM public.verification_runs vr
        JOIN public.submissions s
          ON s.submission_id = vr.submission_id
        WHERE vr.bounty_id = p_bounty_id
          AND vr.status = 'completed'
          AND vr.split_type = 'blind'
        ORDER BY vr.completed_at DESC
    )
    SELECT
        submission_id,
        architect_id,
        metric_values,
        verified_at,
        total_count
    FROM ranked
    LIMIT p_limit
    OFFSET p_offset;
$$;

CREATE OR REPLACE FUNCTION public.get_atom_benchmarks(p_fqdn TEXT)
RETURNS TABLE (
    benchmark_id TEXT,
    benchmark_name TEXT,
    metric_name TEXT,
    metric_value DOUBLE PRECISION,
    dataset_tag TEXT,
    measured_at TIMESTAMPTZ
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT
        ab.benchmark_name AS benchmark_id,
        ab.benchmark_name,
        ab.metric_name,
        ab.metric_value,
        ab.dataset_tag,
        ab.measured_at
    FROM public.atom_benchmarks ab
    JOIN public.atom_versions av ON av.version_id = ab.version_id
    JOIN public.atoms a ON a.atom_id = av.atom_id
    WHERE a.fqdn = p_fqdn
    ORDER BY ab.measured_at DESC;
$$;

CREATE OR REPLACE FUNCTION public.get_manifest_benchmarks()
RETURNS TABLE (
    atom_fqdn TEXT,
    content_hash TEXT,
    benchmark_id TEXT,
    benchmark_name TEXT,
    metric_name TEXT,
    metric_value DOUBLE PRECISION,
    dataset_tag TEXT,
    measured_at TEXT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT
        a.fqdn AS atom_fqdn,
        av.content_hash,
        ab.benchmark_name AS benchmark_id,
        ab.benchmark_name,
        ab.metric_name,
        ab.metric_value,
        ab.dataset_tag,
        ab.measured_at::text AS measured_at
    FROM public.atom_benchmarks ab
    JOIN public.atom_versions av ON av.version_id = ab.version_id
    JOIN public.atoms a ON a.atom_id = av.atom_id
    WHERE a.status = 'approved'
      AND a.is_publishable = TRUE
    ORDER BY a.fqdn, ab.benchmark_name, ab.metric_name;
$$;
