-- Unified artifact model phase 1.
-- Additive scaffold only: preserve the existing atom schema and layer new
-- artifact-facing tables, views, and RPCs beside it.

CREATE TABLE IF NOT EXISTS public.artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_kind TEXT NOT NULL
        CHECK (artifact_kind IN ('atom', 'cdg')),
    fqdn TEXT NOT NULL UNIQUE,
    owner_id UUID REFERENCES public.users(user_id),
    source_repo_id UUID REFERENCES public.atom_source_repositories(source_repo_id),
    namespace_root TEXT NOT NULL DEFAULT '',
    namespace_path TEXT NOT NULL DEFAULT '',
    source_package TEXT NOT NULL DEFAULT '',
    source_module_path TEXT NOT NULL DEFAULT '',
    source_symbol TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'deprecated', 'blocked')),
    visibility_tier TEXT NOT NULL DEFAULT 'general'
        CHECK (visibility_tier IN ('general', 'early_access', 'internal')),
    description TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'hand_written'
        CHECK (source_kind IN ('hand_written', 'generated', 'wrapped', 'ffi')),
    stateful_kind TEXT NOT NULL DEFAULT 'none'
        CHECK (stateful_kind IN ('none', 'argument_state', 'explicit_state_model',
                                 'implicit_stateful', 'return_state')),
    is_stochastic BOOLEAN NOT NULL DEFAULT FALSE,
    is_ffi BOOLEAN NOT NULL DEFAULT FALSE,
    is_publishable BOOLEAN NOT NULL DEFAULT FALSE,
    topo_hash TEXT NOT NULL DEFAULT '',
    top_level_input_arity INTEGER NOT NULL DEFAULT 0,
    top_level_output_arity INTEGER NOT NULL DEFAULT 0,
    leaf_count INTEGER NOT NULL DEFAULT 0,
    verified_leaf_coverage DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_kind
    ON public.artifacts (artifact_kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_fqdn
    ON public.artifacts (fqdn);
CREATE INDEX IF NOT EXISTS idx_artifacts_status
    ON public.artifacts (status);
CREATE INDEX IF NOT EXISTS idx_artifacts_visibility_tier
    ON public.artifacts (visibility_tier);
CREATE INDEX IF NOT EXISTS idx_artifacts_publishable
    ON public.artifacts (is_publishable)
    WHERE is_publishable = TRUE;

CREATE TABLE IF NOT EXISTS public.artifact_versions (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    content_hash TEXT UNIQUE NOT NULL,
    semver TEXT NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT FALSE,
    derives_from UUID REFERENCES public.artifact_versions(version_id),
    s3_key TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, semver)
);

CREATE INDEX IF NOT EXISTS idx_artifact_versions_artifact
    ON public.artifact_versions (artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_hash
    ON public.artifact_versions (content_hash);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_latest
    ON public.artifact_versions (artifact_id)
    WHERE is_latest = TRUE;

CREATE TABLE IF NOT EXISTS public.artifact_io_specs (
    io_spec_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id) ON DELETE SET NULL,
    direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    name TEXT NOT NULL,
    type_desc TEXT NOT NULL DEFAULT 'Any',
    constraints TEXT NOT NULL DEFAULT '',
    required BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL DEFAULT 0,
    UNIQUE (artifact_id, version_id, direction, name)
);

CREATE INDEX IF NOT EXISTS idx_artifact_io_specs_artifact
    ON public.artifact_io_specs (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_parameters (
    parameter_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL
        CHECK (kind IN ('positional_only', 'positional_or_keyword', 'keyword_only', 'varargs', 'kwargs')),
    type_desc TEXT NOT NULL DEFAULT 'Any',
    required BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    technical_description TEXT NOT NULL DEFAULT '',
    dejargonized_description TEXT NOT NULL DEFAULT '',
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (artifact_id, version_id, name)
);

CREATE INDEX IF NOT EXISTS idx_artifact_parameters_artifact
    ON public.artifact_parameters (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_descriptions (
    description_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('technical', 'dejargonized', 'conceptual_summary', 'usage_example')),
    content TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    generated_by TEXT NOT NULL DEFAULT '',
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    jargon_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, kind, language)
);

CREATE INDEX IF NOT EXISTS idx_artifact_descriptions_artifact
    ON public.artifact_descriptions (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_references (
    reference_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    ref_id TEXT NOT NULL REFERENCES public.references_registry(ref_id) ON DELETE CASCADE,
    ref_key TEXT NOT NULL,
    doi TEXT,
    title TEXT NOT NULL,
    authors TEXT[] NOT NULL DEFAULT '{}',
    year INTEGER,
    url TEXT NOT NULL DEFAULT '',
    relevance_note TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT ''
        CHECK (confidence IN ('', 'low', 'medium', 'high')),
    matched_nodes TEXT[] NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'llm_extracted', 'crossref', 'semantic_scholar')),
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, ref_key)
);

CREATE INDEX IF NOT EXISTS idx_artifact_references_artifact
    ON public.artifact_references (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_uncertainty_estimates (
    estimate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id) ON DELETE SET NULL,
    mode TEXT NOT NULL DEFAULT 'empirical'
        CHECK (mode IN ('empirical', 'analytical', 'propagated')),
    scalar_factor DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    n_trials INTEGER NOT NULL DEFAULT 0,
    epsilon DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_regime TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifact_uncertainty_artifact
    ON public.artifact_uncertainty_estimates (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_verification_matches (
    match_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id) ON DELETE SET NULL,
    predicate_id TEXT NOT NULL DEFAULT '',
    predicate_statement TEXT NOT NULL DEFAULT '',
    informal_desc TEXT NOT NULL DEFAULT '',
    candidate_name TEXT NOT NULL DEFAULT '',
    candidate_source_lib TEXT NOT NULL DEFAULT '',
    candidate_score DOUBLE PRECISION,
    retrieval_method TEXT NOT NULL DEFAULT '',
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_level TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_level IN ('kernel_proof', 'type_checked', 'contract_checked', 'unverified')),
    proof_term TEXT NOT NULL DEFAULT '',
    compiler_output TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    all_candidates JSONB NOT NULL DEFAULT '[]',
    all_verifications JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifact_verification_matches_artifact
    ON public.artifact_verification_matches (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_audit_evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id) ON DELETE SET NULL,
    audit_type TEXT NOT NULL
        CHECK (audit_type IN (
            'smoke_test',
            'regression_test',
            'structural_audit',
            'semantic_audit',
            'risk_assessment',
            'parity_check',
            'fuzz_test'
        )),
    passed BOOLEAN NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    details JSONB NOT NULL DEFAULT '{}',
    source_kind TEXT NOT NULL DEFAULT 'automated'
        CHECK (source_kind IN ('automated', 'manual', 'llm_assisted')),
    runner_version TEXT NOT NULL DEFAULT '',
    run_duration_ms INTEGER,
    source_revision TEXT NOT NULL DEFAULT '',
    upstream_version TEXT NOT NULL DEFAULT '',
    review_basis_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifact_audit_evidence_artifact
    ON public.artifact_audit_evidence (artifact_id);

CREATE TABLE IF NOT EXISTS public.artifact_audit_rollups (
    artifact_id UUID PRIMARY KEY REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    overall_verdict TEXT NOT NULL DEFAULT 'unknown'
        CHECK (overall_verdict IN ('unknown', 'trusted', 'acceptable_with_limits',
                                   'limited_acceptability', 'misleading', 'broken')),
    structural_status TEXT NOT NULL DEFAULT 'unknown',
    runtime_status TEXT NOT NULL DEFAULT 'unknown',
    semantic_status TEXT NOT NULL DEFAULT 'unknown',
    developer_semantics_status TEXT NOT NULL DEFAULT 'unknown',
    risk_tier TEXT NOT NULL DEFAULT 'medium'
        CHECK (risk_tier IN ('low', 'medium', 'high')),
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_reasons TEXT[] NOT NULL DEFAULT '{}',
    acceptability_score INTEGER NOT NULL DEFAULT 0,
    acceptability_band TEXT NOT NULL DEFAULT 'unknown'
        CHECK (acceptability_band IN ('unknown', 'acceptable_with_limits',
                                      'acceptable_with_limits_candidate',
                                      'limited_acceptability')),
    parity_coverage_level TEXT NOT NULL DEFAULT 'unknown'
        CHECK (parity_coverage_level IN ('unknown', 'none', 'not_applicable',
                                         'positive_path', 'positive_and_negative',
                                         'parity_or_usage_equivalent')),
    parity_test_status TEXT NOT NULL DEFAULT 'unknown',
    parity_fixture_count INTEGER NOT NULL DEFAULT 0,
    parity_case_count INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'missing',
    review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_limitations TEXT[] NOT NULL DEFAULT '{}',
    review_required_actions TEXT[] NOT NULL DEFAULT '{}',
    trust_readiness TEXT NOT NULL DEFAULT 'not_ready',
    trust_blockers TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.artifact_hyperparams (
    hp_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('int', 'float', 'categorical', 'bool')),
    default_value JSONB,
    min_value JSONB,
    max_value JSONB,
    step_value JSONB,
    log_scale BOOLEAN NOT NULL DEFAULT FALSE,
    choices_json JSONB,
    constraints_json JSONB,
    semantic_role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK (status IN ('approved', 'blocked', 'deprecated')),
    UNIQUE (artifact_id, name)
);

CREATE TABLE IF NOT EXISTS public.artifact_benchmarks (
    benchmark_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id) ON DELETE CASCADE,
    benchmark_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    dataset_tag TEXT NOT NULL DEFAULT '',
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifact_benchmarks_version
    ON public.artifact_benchmarks (version_id);

CREATE TABLE IF NOT EXISTS public.artifact_cdg_nodes (
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    parent_node_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    concept_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    type_signature TEXT NOT NULL DEFAULT '',
    matched_primitive TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (version_id, node_id)
);

CREATE TABLE IF NOT EXISTS public.artifact_cdg_edges (
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    output_name TEXT NOT NULL DEFAULT '',
    input_name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (version_id, source_id, target_id, output_name, input_name)
);

CREATE TABLE IF NOT EXISTS public.artifact_cdg_bindings (
    binding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    bound_artifact_fqdn TEXT NOT NULL DEFAULT '',
    bound_version_content_hash TEXT NOT NULL DEFAULT '',
    binding_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    binding_source TEXT NOT NULL DEFAULT '',
    UNIQUE (version_id, node_id, bound_artifact_fqdn)
);

CREATE OR REPLACE FUNCTION public.artifact_is_publishable(check_artifact_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
WITH base AS (
    SELECT
        a.artifact_kind,
        a.top_level_input_arity,
        a.top_level_output_arity,
        a.verified_leaf_coverage
    FROM public.artifacts a
    WHERE a.artifact_id = check_artifact_id
)
SELECT COALESCE((
    EXISTS (
        SELECT 1 FROM base
    )
    AND (
        EXISTS (
            SELECT 1
            FROM public.artifact_io_specs ios
            WHERE ios.artifact_id = check_artifact_id
        )
        OR EXISTS (
            SELECT 1
            FROM base
            WHERE top_level_input_arity > 0 OR top_level_output_arity > 0
        )
    )
    AND EXISTS (
        SELECT 1
        FROM public.artifact_descriptions d
        WHERE d.artifact_id = check_artifact_id
          AND d.kind = 'dejargonized'
          AND d.language = 'en'
    )
    AND EXISTS (
        SELECT 1
        FROM public.artifact_references r
        WHERE r.artifact_id = check_artifact_id
    )
    AND EXISTS (
        SELECT 1
        FROM public.artifact_audit_rollups ar
        WHERE ar.artifact_id = check_artifact_id
    )
    AND (
        NOT EXISTS (
            SELECT 1
            FROM base
            WHERE artifact_kind = 'cdg'
        )
        OR EXISTS (
            SELECT 1
            FROM public.artifact_cdg_nodes n
            JOIN public.artifact_versions v ON v.version_id = n.version_id
            WHERE v.artifact_id = check_artifact_id
        )
    )
    AND (
        NOT EXISTS (
            SELECT 1
            FROM base
            WHERE artifact_kind = 'cdg'
        )
        OR EXISTS (
            SELECT 1
            FROM public.artifact_cdg_bindings b
            JOIN public.artifact_versions v ON v.version_id = b.version_id
            WHERE v.artifact_id = check_artifact_id
        )
        OR EXISTS (
            SELECT 1
            FROM base
            WHERE verified_leaf_coverage > 0
        )
    )
), FALSE);
$$;

DROP VIEW IF EXISTS public.catalog_artifacts_served CASCADE;
CREATE VIEW public.catalog_artifacts_served
WITH (security_invoker = true)
AS
SELECT
    a.atom_id AS artifact_id,
    'atom'::TEXT AS artifact_kind,
    a.fqdn,
    a.namespace_root,
    a.namespace_path,
    a.source_package,
    a.source_module_path,
    a.source_symbol,
    a.source_kind,
    a.stateful_kind,
    a.is_stochastic,
    a.is_ffi,
    a.domain_tags,
    a.visibility_tier,
    a.description AS technical_description,
    d.content AS dejargonized_description,
    d.jargon_score,
    ar.overall_verdict,
    ar.risk_tier,
    ar.risk_score,
    ar.acceptability_score,
    ar.acceptability_band,
    ar.parity_coverage_level,
    ar.trust_readiness,
    ar.review_status,
    COALESCE(ref_counts.reference_count, 0) AS reference_count
FROM public.atoms a
LEFT JOIN public.atom_descriptions d
  ON d.atom_id = a.atom_id
 AND d.kind = 'dejargonized'
 AND d.language = 'en'
LEFT JOIN public.atom_audit_rollups ar
  ON ar.atom_id = a.atom_id
LEFT JOIN (
    SELECT atom_id, COUNT(*) AS reference_count
    FROM public.atom_references
    GROUP BY atom_id
) AS ref_counts
  ON ref_counts.atom_id = a.atom_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE
UNION ALL
SELECT
    a.artifact_id,
    a.artifact_kind,
    a.fqdn,
    a.namespace_root,
    a.namespace_path,
    a.source_package,
    a.source_module_path,
    a.source_symbol,
    a.source_kind,
    a.stateful_kind,
    a.is_stochastic,
    a.is_ffi,
    ARRAY[]::text[] AS domain_tags,
    a.visibility_tier,
    a.description AS technical_description,
    d.content AS dejargonized_description,
    d.jargon_score,
    ar.overall_verdict,
    ar.risk_tier,
    ar.risk_score,
    ar.acceptability_score,
    ar.acceptability_band,
    ar.parity_coverage_level,
    ar.trust_readiness,
    ar.review_status,
    COALESCE(ref_counts.reference_count, 0) AS reference_count
FROM public.artifacts a
LEFT JOIN public.artifact_descriptions d
  ON d.artifact_id = a.artifact_id
 AND d.kind = 'dejargonized'
 AND d.language = 'en'
LEFT JOIN public.artifact_audit_rollups ar
  ON ar.artifact_id = a.artifact_id
LEFT JOIN (
    SELECT artifact_id, COUNT(*) AS reference_count
    FROM public.artifact_references
    GROUP BY artifact_id
) AS ref_counts
  ON ref_counts.artifact_id = a.artifact_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE;

GRANT SELECT ON public.catalog_artifacts_served TO authenticated;

CREATE OR REPLACE FUNCTION public.get_artifact_document(request_fqdn TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
WITH matched_artifact AS (
    SELECT a.*
    FROM public.artifacts a
    WHERE a.fqdn = request_fqdn
    LIMIT 1
)
SELECT CASE
    WHEN EXISTS (SELECT 1 FROM matched_artifact) THEN (
        SELECT jsonb_build_object(
            'artifact', row_to_json(a),
            'source_repository', (
                SELECT row_to_json(sr)
                FROM public.atom_source_repositories sr
                WHERE sr.source_repo_id = a.source_repo_id
            ),
            'descriptions', (
                SELECT jsonb_agg(row_to_json(d) ORDER BY d.kind, d.language)
                FROM public.artifact_descriptions d
                WHERE d.artifact_id = a.artifact_id
            ),
            'io_specs', (
                SELECT jsonb_agg(row_to_json(ios) ORDER BY ios.direction, ios.ordinal)
                FROM public.artifact_io_specs ios
                WHERE ios.artifact_id = a.artifact_id
            ),
            'parameters', (
                SELECT jsonb_agg(row_to_json(p) ORDER BY p.position)
                FROM public.artifact_parameters p
                WHERE p.artifact_id = a.artifact_id
            ),
            'references', (
                SELECT jsonb_agg(row_to_json(r) ORDER BY r.year NULLS LAST, r.title)
                FROM public.artifact_references r
                WHERE r.artifact_id = a.artifact_id
            ),
            'audit_rollup', (
                SELECT row_to_json(ar)
                FROM public.artifact_audit_rollups ar
                WHERE ar.artifact_id = a.artifact_id
            ),
            'audit_latest', (
                SELECT jsonb_agg(row_to_json(latest_row) ORDER BY latest_row.audit_type)
                FROM (
                    SELECT DISTINCT ON (e.audit_type)
                        e.artifact_id,
                        e.audit_type,
                        e.passed,
                        e.status,
                        e.details,
                        e.source_kind,
                        e.runner_version,
                        e.source_revision,
                        e.upstream_version,
                        e.created_at AS audited_at
                    FROM public.artifact_audit_evidence e
                    WHERE e.artifact_id = a.artifact_id
                    ORDER BY e.audit_type, e.created_at DESC
                ) AS latest_row
            ),
            'uncertainty_estimates', (
                SELECT jsonb_agg(row_to_json(ue) ORDER BY ue.created_at DESC)
                FROM public.artifact_uncertainty_estimates ue
                WHERE ue.artifact_id = a.artifact_id
            ),
            'verification_matches', (
                SELECT jsonb_agg(
                    row_to_json(vm)
                    ORDER BY vm.verification_level, vm.candidate_score DESC NULLS LAST
                )
                FROM public.artifact_verification_matches vm
                WHERE vm.artifact_id = a.artifact_id
            ),
            'cdg_nodes', (
                SELECT jsonb_agg(row_to_json(n) ORDER BY n.node_id)
                FROM public.artifact_cdg_nodes n
                JOIN public.artifact_versions v ON v.version_id = n.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'cdg_edges', (
                SELECT jsonb_agg(row_to_json(e) ORDER BY e.source_id, e.target_id)
                FROM public.artifact_cdg_edges e
                JOIN public.artifact_versions v ON v.version_id = e.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'cdg_bindings', (
                SELECT jsonb_agg(row_to_json(b) ORDER BY b.node_id, b.bound_artifact_fqdn)
                FROM public.artifact_cdg_bindings b
                JOIN public.artifact_versions v ON v.version_id = b.version_id
                WHERE v.artifact_id = a.artifact_id
            )
        )
        FROM matched_artifact a
    )
    ELSE public.get_atom_document(request_fqdn)
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_artifact_document(TEXT) TO authenticated;

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
    SELECT
        cas.artifact_id,
        cas.artifact_kind,
        cas.fqdn,
        cas.technical_description,
        cas.domain_tags,
        cas.overall_verdict,
        cas.risk_tier,
        cas.trust_readiness,
        CASE
            WHEN query_text = '' THEN 0.0
            WHEN lower(cas.fqdn) = lower(query_text) THEN 1.0
            WHEN lower(cas.fqdn) LIKE '%' || lower(query_text) || '%' THEN 0.8
            WHEN lower(COALESCE(cas.technical_description, '')) LIKE '%' || lower(query_text) || '%' THEN 0.6
            ELSE 0.2
        END AS score
    FROM public.catalog_artifacts_served cas
    WHERE query_text = ''
       OR lower(cas.fqdn) LIKE '%' || lower(query_text) || '%'
       OR lower(COALESCE(cas.technical_description, '')) LIKE '%' || lower(query_text) || '%'
    ORDER BY score DESC, cas.fqdn
    LIMIT GREATEST(result_limit, 0)
    OFFSET GREATEST(result_offset, 0);
$$;

GRANT EXECUTE ON FUNCTION public.search_artifacts_hybrid(TEXT, TEXT, INTEGER, INTEGER)
    TO authenticated;
