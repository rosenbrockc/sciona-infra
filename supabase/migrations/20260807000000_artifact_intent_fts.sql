-- Give published artifacts the same relaxed natural-language recall as atoms.

CREATE OR REPLACE FUNCTION public.search_artifacts_hybrid(
    query_text TEXT,
    mode TEXT DEFAULT 'fts',
    result_limit INTEGER DEFAULT 50,
    result_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    artifact_id UUID,
    artifact_kind TEXT,
    fqdn TEXT,
    technical_description TEXT,
    domain_tags TEXT[],
    overall_verdict TEXT,
    risk_tier TEXT,
    trust_readiness TEXT,
    score DOUBLE PRECISION
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH query_input AS (
        SELECT
            websearch_to_tsquery(
                'english', NULLIF(BTRIM(query_text), '')
            ) AS strict_tsq,
            public.catalog_relaxed_tsquery(query_text) AS relaxed_tsq
    ),
    candidates AS (
        SELECT
            cas.*,
            public.catalog_search_document(
                cas.fqdn,
                cas.technical_description,
                '',
                cas.domain_tags
            ) AS document,
            query_input.strict_tsq,
            query_input.relaxed_tsq
        FROM public.catalog_artifacts_served cas
        CROSS JOIN query_input
    )
    SELECT
        candidates.artifact_id,
        candidates.artifact_kind,
        candidates.fqdn,
        candidates.technical_description,
        candidates.domain_tags,
        candidates.overall_verdict,
        candidates.risk_tier,
        candidates.trust_readiness,
        (
            ts_rank_cd(candidates.document, candidates.relaxed_tsq)
            + CASE
                WHEN candidates.strict_tsq IS NOT NULL
                 AND candidates.document @@ candidates.strict_tsq
                THEN 1.0
                ELSE 0.0
              END
            + CASE
                WHEN lower(candidates.fqdn) = lower(query_text) THEN 2.0
                ELSE 0.0
              END
        )::DOUBLE PRECISION AS score
    FROM candidates
    WHERE BTRIM(COALESCE(query_text, '')) = ''
       OR (
            candidates.relaxed_tsq IS NOT NULL
        AND candidates.document @@ candidates.relaxed_tsq
       )
    ORDER BY score DESC, candidates.fqdn
    LIMIT GREATEST(result_limit, 0)
    OFFSET GREATEST(result_offset, 0);
$$;

GRANT EXECUTE ON FUNCTION public.search_artifacts_hybrid(TEXT, TEXT, INTEGER, INTEGER)
    TO anon, authenticated;
