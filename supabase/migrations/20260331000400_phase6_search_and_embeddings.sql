-- Phase 6: Hybrid search and vector embeddings.
-- Adds RLS-safe search RPCs, a dedicated embedding store, and an event queue.

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

-- Preserve anon access parity with the existing public catalog/document endpoints.
GRANT SELECT ON public.catalog_atoms_served TO anon;

-- ============================================================
-- Search / embedding helper functions
-- ============================================================

CREATE OR REPLACE FUNCTION public.catalog_search_document(
    p_fqdn TEXT,
    p_technical_description TEXT,
    p_dejargonized_description TEXT,
    p_domain_tags TEXT[]
)
RETURNS tsvector
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT
        setweight(to_tsvector('english', COALESCE(p_fqdn, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(p_technical_description, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(p_dejargonized_description, '')), 'C') ||
        setweight(
            to_tsvector('english', array_to_string(COALESCE(p_domain_tags, '{}'::TEXT[]), ' ')),
            'B'
        );
$$;

CREATE OR REPLACE FUNCTION public.atom_embedding_input_text(
    p_fqdn TEXT,
    p_technical_description TEXT,
    p_dejargonized_description TEXT,
    p_domain_tags TEXT[]
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT array_to_string(
        array_remove(
            ARRAY[
                NULLIF(COALESCE(p_fqdn, ''), ''),
                NULLIF(COALESCE(p_technical_description, ''), ''),
                NULLIF(COALESCE(p_dejargonized_description, ''), ''),
                NULLIF(array_to_string(COALESCE(p_domain_tags, '{}'::TEXT[]), ' '), '')
            ],
            NULL
        ),
        E'\n'
    );
$$;

CREATE OR REPLACE FUNCTION public.atom_embedding_input_hash(
    p_fqdn TEXT,
    p_technical_description TEXT,
    p_dejargonized_description TEXT,
    p_domain_tags TEXT[]
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT SUBSTRING(
        encode(
            extensions.digest(
                public.atom_embedding_input_text(
                    p_fqdn,
                    p_technical_description,
                    p_dejargonized_description,
                    p_domain_tags
                ),
                'sha256'
            ),
            'hex'
        )
        FROM 1 FOR 16
    );
$$;

-- ============================================================
-- Atom embeddings
-- ============================================================

CREATE TABLE public.atom_embeddings (
    atom_id UUID PRIMARY KEY REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    embedding extensions.vector(1536) NOT NULL,
    model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    dimensions INTEGER NOT NULL DEFAULT 1536 CHECK (dimensions > 0),
    input_text_hash TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_atom_embeddings_hnsw
    ON public.atom_embeddings
    USING hnsw (embedding extensions.vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

ALTER TABLE public.atom_embeddings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS atom_embeddings_select_visible ON public.atom_embeddings;
CREATE POLICY atom_embeddings_select_visible ON public.atom_embeddings
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.atoms a
            WHERE a.atom_id = atom_embeddings.atom_id
        )
    );

REVOKE ALL ON TABLE public.atom_embeddings FROM PUBLIC;
GRANT SELECT ON TABLE public.atom_embeddings TO anon, authenticated;

-- ============================================================
-- Embedding refresh queue
-- ============================================================

CREATE TABLE public.embedding_refresh_queue (
    queue_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT 'content_changed',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    error_message TEXT NOT NULL DEFAULT '',
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_embedding_queue_pending
    ON public.embedding_refresh_queue (status, enqueued_at)
    WHERE status = 'pending';

CREATE INDEX idx_embedding_queue_atom
    ON public.embedding_refresh_queue (atom_id);

CREATE UNIQUE INDEX ux_embedding_queue_pending_atom
    ON public.embedding_refresh_queue (atom_id)
    WHERE status = 'pending';

ALTER TABLE public.embedding_refresh_queue ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.embedding_refresh_queue FROM PUBLIC, anon, authenticated;

-- ============================================================
-- Queue maintenance trigger
-- ============================================================

CREATE OR REPLACE FUNCTION public.enqueue_embedding_refresh()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    target_atom_id UUID;
    queue_reason TEXT := 'content_changed';
BEGIN
    target_atom_id := COALESCE(NEW.atom_id, OLD.atom_id);

    IF TG_TABLE_NAME = 'atom_descriptions' THEN
        IF COALESCE(NEW.kind, OLD.kind) <> 'dejargonized'
           OR COALESCE(NEW.language, OLD.language) <> 'en' THEN
            RETURN NULL;
        END IF;
        queue_reason := 'description_changed';
    ELSIF TG_TABLE_NAME = 'atoms' THEN
        queue_reason := 'atom_content_changed';
    END IF;

    INSERT INTO public.embedding_refresh_queue (atom_id, reason)
    VALUES (target_atom_id, queue_reason)
    ON CONFLICT DO NOTHING;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_embedding_refresh_atoms ON public.atoms;
CREATE TRIGGER trg_embedding_refresh_atoms
    AFTER INSERT OR UPDATE OF fqdn, description, domain_tags ON public.atoms
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_embedding_refresh();

DROP TRIGGER IF EXISTS trg_embedding_refresh_descriptions ON public.atom_descriptions;
CREATE TRIGGER trg_embedding_refresh_descriptions
    AFTER INSERT OR UPDATE OF content, kind, language ON public.atom_descriptions
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_embedding_refresh();

-- ============================================================
-- Search RPCs
-- ============================================================

CREATE OR REPLACE FUNCTION public.search_atoms_fts(
    query_text TEXT,
    result_limit INTEGER DEFAULT 20,
    result_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[],
    overall_verdict TEXT,
    risk_tier TEXT,
    trust_readiness TEXT,
    fts_rank REAL
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH query_input AS (
        SELECT websearch_to_tsquery('english', NULLIF(BTRIM(query_text), '')) AS tsq
    )
    SELECT
        ci.atom_id,
        ci.fqdn,
        ci.technical_description,
        ci.dejargonized_description,
        ci.domain_tags,
        ci.overall_verdict,
        ci.risk_tier,
        ci.trust_readiness,
        ts_rank_cd(
            public.catalog_search_document(
                ci.fqdn,
                ci.technical_description,
                ci.dejargonized_description,
                ci.domain_tags
            ),
            query_input.tsq
        ) AS fts_rank
    FROM public.catalog_atoms_served ci
    CROSS JOIN query_input
    WHERE query_input.tsq IS NOT NULL
      AND public.catalog_search_document(
            ci.fqdn,
            ci.technical_description,
            ci.dejargonized_description,
            ci.domain_tags
        ) @@ query_input.tsq
    ORDER BY fts_rank DESC, ci.fqdn
    LIMIT result_limit
    OFFSET result_offset;
$$;

CREATE OR REPLACE FUNCTION public.search_atoms_vector(
    query_embedding extensions.vector(1536),
    result_limit INTEGER DEFAULT 20,
    result_offset INTEGER DEFAULT 0,
    similarity_threshold DOUBLE PRECISION DEFAULT 0.3
)
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[],
    overall_verdict TEXT,
    risk_tier TEXT,
    trust_readiness TEXT,
    similarity DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
    PERFORM set_config('hnsw.ef_search', '40', true);

    RETURN QUERY
    SELECT
        ci.atom_id,
        ci.fqdn,
        ci.technical_description,
        ci.dejargonized_description,
        ci.domain_tags,
        ci.overall_verdict,
        ci.risk_tier,
        ci.trust_readiness,
        1 - (ae.embedding OPERATOR(extensions.<=>) query_embedding) AS similarity
    FROM public.atom_embeddings ae
    JOIN public.catalog_atoms_served ci
      ON ci.atom_id = ae.atom_id
    WHERE 1 - (ae.embedding OPERATOR(extensions.<=>) query_embedding) >= similarity_threshold
    ORDER BY ae.embedding OPERATOR(extensions.<=>) query_embedding, ci.fqdn
    LIMIT result_limit
    OFFSET result_offset;
END;
$$;

CREATE OR REPLACE FUNCTION public.search_atoms_hybrid(
    query_text TEXT,
    query_embedding extensions.vector(1536) DEFAULT NULL,
    mode TEXT DEFAULT 'hybrid',
    result_limit INTEGER DEFAULT 20,
    result_offset INTEGER DEFAULT 0,
    fts_weight DOUBLE PRECISION DEFAULT 1.0,
    vector_weight DOUBLE PRECISION DEFAULT 1.0,
    rrf_k INTEGER DEFAULT 60,
    similarity_threshold DOUBLE PRECISION DEFAULT 0.3
)
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[],
    overall_verdict TEXT,
    risk_tier TEXT,
    trust_readiness TEXT,
    fts_rank REAL,
    similarity DOUBLE PRECISION,
    hybrid_score DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    normalized_mode TEXT := LOWER(COALESCE(mode, 'hybrid'));
BEGIN
    IF normalized_mode = 'fts' THEN
        RETURN QUERY
        SELECT
            f.atom_id,
            f.fqdn,
            f.technical_description,
            f.dejargonized_description,
            f.domain_tags,
            f.overall_verdict,
            f.risk_tier,
            f.trust_readiness,
            f.fts_rank,
            NULL::DOUBLE PRECISION AS similarity,
            f.fts_rank::DOUBLE PRECISION AS hybrid_score
        FROM public.search_atoms_fts(query_text, result_limit, result_offset) AS f;
        RETURN;
    END IF;

    IF normalized_mode = 'vector' THEN
        IF query_embedding IS NULL THEN
            RAISE EXCEPTION 'query_embedding is required for vector mode';
        END IF;

        RETURN QUERY
        SELECT
            v.atom_id,
            v.fqdn,
            v.technical_description,
            v.dejargonized_description,
            v.domain_tags,
            v.overall_verdict,
            v.risk_tier,
            v.trust_readiness,
            NULL::REAL AS fts_rank,
            v.similarity,
            v.similarity AS hybrid_score
        FROM public.search_atoms_vector(
            query_embedding,
            result_limit,
            result_offset,
            similarity_threshold
        ) AS v;
        RETURN;
    END IF;

    IF normalized_mode <> 'hybrid' THEN
        RAISE EXCEPTION 'mode must be one of fts, vector, or hybrid';
    END IF;

    IF query_embedding IS NULL THEN
        RAISE EXCEPTION 'query_embedding is required for hybrid mode';
    END IF;

    PERFORM set_config('hnsw.ef_search', '40', true);

    RETURN QUERY
    WITH query_input AS (
        SELECT websearch_to_tsquery('english', NULLIF(BTRIM(query_text), '')) AS tsq
    ),
    fts_results AS (
        SELECT
            ci.atom_id,
            ROW_NUMBER() OVER (
                ORDER BY
                    ts_rank_cd(
                        public.catalog_search_document(
                            ci.fqdn,
                            ci.technical_description,
                            ci.dejargonized_description,
                            ci.domain_tags
                        ),
                        query_input.tsq
                    ) DESC,
                    ci.fqdn
            ) AS fts_row_rank,
            ts_rank_cd(
                public.catalog_search_document(
                    ci.fqdn,
                    ci.technical_description,
                    ci.dejargonized_description,
                    ci.domain_tags
                ),
                query_input.tsq
            ) AS fts_rank_score
        FROM public.catalog_atoms_served ci
        CROSS JOIN query_input
        WHERE query_input.tsq IS NOT NULL
          AND public.catalog_search_document(
                ci.fqdn,
                ci.technical_description,
                ci.dejargonized_description,
                ci.domain_tags
            ) @@ query_input.tsq
    ),
    vector_results AS (
        SELECT
            ae.atom_id,
            ROW_NUMBER() OVER (
                ORDER BY ae.embedding OPERATOR(extensions.<=>) query_embedding, ci.fqdn
            ) AS vec_row_rank,
            1 - (ae.embedding OPERATOR(extensions.<=>) query_embedding) AS vec_similarity
        FROM public.atom_embeddings ae
        JOIN public.catalog_atoms_served ci
          ON ci.atom_id = ae.atom_id
        WHERE 1 - (ae.embedding OPERATOR(extensions.<=>) query_embedding) >= similarity_threshold
    ),
    combined AS (
        SELECT
            COALESCE(f.atom_id, v.atom_id) AS atom_id,
            f.fts_rank_score,
            v.vec_similarity,
            COALESCE(fts_weight * (1.0 / (rrf_k + f.fts_row_rank)), 0) +
            COALESCE(vector_weight * (1.0 / (rrf_k + v.vec_row_rank)), 0) AS rrf_score
        FROM fts_results f
        FULL OUTER JOIN vector_results v
          ON v.atom_id = f.atom_id
    )
    SELECT
        ci.atom_id,
        ci.fqdn,
        ci.technical_description,
        ci.dejargonized_description,
        ci.domain_tags,
        ci.overall_verdict,
        ci.risk_tier,
        ci.trust_readiness,
        combined.fts_rank_score::REAL AS fts_rank,
        combined.vec_similarity AS similarity,
        combined.rrf_score AS hybrid_score
    FROM combined
    JOIN public.catalog_atoms_served ci
      ON ci.atom_id = combined.atom_id
    ORDER BY combined.rrf_score DESC, ci.fqdn
    LIMIT result_limit
    OFFSET result_offset;
END;
$$;

GRANT EXECUTE ON FUNCTION public.search_atoms_fts(TEXT, INTEGER, INTEGER)
    TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_atoms_vector(
    extensions.vector,
    INTEGER,
    INTEGER,
    DOUBLE PRECISION
) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_atoms_hybrid(
    TEXT,
    extensions.vector,
    TEXT,
    INTEGER,
    INTEGER,
    DOUBLE PRECISION,
    DOUBLE PRECISION,
    INTEGER,
    DOUBLE PRECISION
) TO anon, authenticated;

-- ============================================================
-- Service helper RPCs
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_atoms_needing_embeddings()
RETURNS TABLE (
    atom_id UUID,
    fqdn TEXT,
    technical_description TEXT,
    dejargonized_description TEXT,
    domain_tags TEXT[]
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT
        ci.atom_id,
        ci.fqdn,
        ci.technical_description,
        ci.dejargonized_description,
        ci.domain_tags
    FROM public.catalog_atoms_served ci
    LEFT JOIN public.atom_embeddings ae
      ON ae.atom_id = ci.atom_id
    WHERE ae.atom_id IS NULL
       OR ae.input_text_hash IS DISTINCT FROM public.atom_embedding_input_hash(
            ci.fqdn,
            ci.technical_description,
            ci.dejargonized_description,
            ci.domain_tags
        )
    ORDER BY ci.fqdn;
$$;

CREATE OR REPLACE FUNCTION public.refresh_catalog_index()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY public.catalog_atoms_index;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.get_atoms_needing_embeddings() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.get_atoms_needing_embeddings() FROM anon;
REVOKE EXECUTE ON FUNCTION public.get_atoms_needing_embeddings() FROM authenticated;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM anon;
REVOKE EXECUTE ON FUNCTION public.refresh_catalog_index() FROM authenticated;
