-- Stateful NLP atoms phase 1.
-- Extend the unified artifact registry with immutable pure-data state artifacts,
-- asset manifests, typed state ports, dependency pins, and state-aware RPCs.

ALTER TABLE public.artifacts
    DROP CONSTRAINT IF EXISTS artifacts_artifact_kind_check;
ALTER TABLE public.artifacts
    ADD CONSTRAINT artifacts_artifact_kind_check
    CHECK (artifact_kind IN ('atom', 'cdg', 'state_artifact'));

ALTER TABLE public.artifacts
    DROP CONSTRAINT IF EXISTS artifacts_state_artifact_shape_check;
ALTER TABLE public.artifacts
    ADD CONSTRAINT artifacts_state_artifact_shape_check
    CHECK (
        artifact_kind <> 'state_artifact'
        OR (
            source_module_path = ''
            AND source_symbol = ''
            AND stateful_kind = 'explicit_state_model'
            AND is_stochastic = FALSE
            AND is_ffi = FALSE
        )
    );

CREATE TABLE IF NOT EXISTS public.artifact_assets (
    asset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    asset_path TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    format TEXT NOT NULL
        CHECK (format IN (
            'safetensors', 'onnx', 'json', 'jsonl', 'parquet',
            'npy', 'npz', 'txt', 'vocab'
        )),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    storage_uri TEXT NOT NULL DEFAULT '',
    compression TEXT NOT NULL DEFAULT '',
    mmap_safe BOOLEAN NOT NULL DEFAULT FALSE,
    loader_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id, asset_path),
    UNIQUE (version_id, sha256)
);

CREATE TABLE IF NOT EXISTS public.state_artifact_metadata (
    version_id UUID PRIMARY KEY REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    resource_family TEXT NOT NULL DEFAULT '',
    language_tags TEXT[] NOT NULL DEFAULT '{}',
    vocabulary_size INTEGER,
    embedding_dim INTEGER,
    max_sequence_length INTEGER,
    label_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    training_data_summary TEXT NOT NULL DEFAULT '',
    provenance_summary TEXT NOT NULL DEFAULT '',
    intended_use TEXT NOT NULL DEFAULT '',
    limitations TEXT[] NOT NULL DEFAULT '{}',
    legal_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_output_precision INTEGER NOT NULL DEFAULT 6,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.artifact_state_ports (
    state_port_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    port_name TEXT NOT NULL,
    type_desc TEXT NOT NULL,
    accepted_formats TEXT[] NOT NULL DEFAULT '{}',
    required_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    ordinal INTEGER NOT NULL DEFAULT 0,
    UNIQUE (artifact_id, version_id, port_name)
);

CREATE TABLE IF NOT EXISTS public.artifact_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dependent_version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    dependency_artifact_fqdn TEXT NOT NULL,
    dependency_content_hash TEXT NOT NULL CHECK (dependency_content_hash ~ '^[0-9a-f]{64}$'),
    dependency_role TEXT NOT NULL
        CHECK (dependency_role IN ('state_artifact', 'logic_atom', 'cdg')),
    port_name TEXT NOT NULL DEFAULT '',
    optional BOOLEAN NOT NULL DEFAULT FALSE,
    binding_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (
        dependent_version_id,
        dependency_artifact_fqdn,
        dependency_content_hash,
        port_name
    )
);

CREATE INDEX IF NOT EXISTS idx_artifact_assets_version
    ON public.artifact_assets (version_id);
CREATE INDEX IF NOT EXISTS idx_artifact_assets_sha256
    ON public.artifact_assets (sha256);
CREATE INDEX IF NOT EXISTS idx_state_metadata_family
    ON public.state_artifact_metadata (resource_family);
CREATE INDEX IF NOT EXISTS idx_state_ports_artifact
    ON public.artifact_state_ports (artifact_id);
CREATE INDEX IF NOT EXISTS idx_dependencies_dependent
    ON public.artifact_dependencies (dependent_version_id);
CREATE INDEX IF NOT EXISTS idx_dependencies_fqdn
    ON public.artifact_dependencies (dependency_artifact_fqdn);

ALTER TABLE public.artifact_audit_evidence
    DROP CONSTRAINT IF EXISTS artifact_audit_evidence_audit_type_check;
ALTER TABLE public.artifact_audit_evidence
    ADD CONSTRAINT artifact_audit_evidence_audit_type_check
    CHECK (audit_type IN (
        'smoke_test',
        'regression_test',
        'structural_audit',
        'semantic_audit',
        'risk_assessment',
        'parity_check',
        'fuzz_test',
        'asset_integrity_check',
        'format_security_scan',
        'loader_policy_check',
        'provenance_review',
        'license_ip_review',
        'privacy_review',
        'golden_eval',
        'determinism_replay',
        'boundary_review'
    ));

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
),
common_requirements AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM public.artifact_descriptions d
            WHERE d.artifact_id = check_artifact_id
              AND d.kind = 'dejargonized'
              AND d.language = 'en'
        ) AS has_dejargonized_description,
        EXISTS (
            SELECT 1
            FROM public.artifact_references r
            WHERE r.artifact_id = check_artifact_id
        ) AS has_reference,
        EXISTS (
            SELECT 1
            FROM public.artifact_audit_rollups ar
            WHERE ar.artifact_id = check_artifact_id
        ) AS has_audit_rollup
),
version_requirements AS (
    SELECT
        EXISTS (
            SELECT 1
            FROM public.artifact_assets aa
            JOIN public.artifact_versions v ON v.version_id = aa.version_id
            WHERE v.artifact_id = check_artifact_id
        ) AS has_asset,
        EXISTS (
            SELECT 1
            FROM public.state_artifact_metadata sam
            JOIN public.artifact_versions v ON v.version_id = sam.version_id
            WHERE v.artifact_id = check_artifact_id
        ) AS has_state_metadata
)
SELECT COALESCE((
    EXISTS (SELECT 1 FROM base)
    AND (SELECT has_dejargonized_description FROM common_requirements)
    AND (SELECT has_reference FROM common_requirements)
    AND (SELECT has_audit_rollup FROM common_requirements)
    AND (
        (
            EXISTS (SELECT 1 FROM base WHERE artifact_kind = 'state_artifact')
            AND (SELECT has_asset FROM version_requirements)
            AND (SELECT has_state_metadata FROM version_requirements)
        )
        OR (
            EXISTS (SELECT 1 FROM base WHERE artifact_kind <> 'state_artifact')
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
    COALESCE(ref_counts.reference_count, 0) AS reference_count,
    ''::TEXT AS resource_family,
    ARRAY[]::TEXT[] AS language_tags
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
    ARRAY[]::TEXT[] AS domain_tags,
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
    COALESCE(ref_counts.reference_count, 0) AS reference_count,
    ''::TEXT AS resource_family,
    ARRAY[]::TEXT[] AS language_tags
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
  AND a.is_publishable = TRUE
  AND a.artifact_kind <> 'state_artifact'
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
    ARRAY[]::TEXT[] AS domain_tags,
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
    COALESCE(ref_counts.reference_count, 0) AS reference_count,
    COALESCE(sam.resource_family, '') AS resource_family,
    COALESCE(sam.language_tags, ARRAY[]::TEXT[]) AS language_tags
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
LEFT JOIN public.artifact_versions v
  ON v.artifact_id = a.artifact_id
 AND v.is_latest = TRUE
LEFT JOIN public.state_artifact_metadata sam
  ON sam.version_id = v.version_id
WHERE a.status = 'approved'
  AND a.is_publishable = TRUE
  AND a.artifact_kind = 'state_artifact';

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
            ),
            'assets', (
                SELECT jsonb_agg(row_to_json(aa) ORDER BY aa.asset_path)
                FROM public.artifact_assets aa
                JOIN public.artifact_versions v ON v.version_id = aa.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'state_metadata', (
                SELECT jsonb_agg(row_to_json(sam) ORDER BY v.created_at DESC)
                FROM public.state_artifact_metadata sam
                JOIN public.artifact_versions v ON v.version_id = sam.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'state_ports', (
                SELECT jsonb_agg(row_to_json(sp) ORDER BY sp.ordinal, sp.port_name)
                FROM public.artifact_state_ports sp
                WHERE sp.artifact_id = a.artifact_id
            ),
            'dependencies', (
                SELECT jsonb_agg(
                    row_to_json(dep)
                    ORDER BY dep.dependency_role, dep.dependency_artifact_fqdn, dep.port_name
                )
                FROM public.artifact_dependencies dep
                JOIN public.artifact_versions v ON v.version_id = dep.dependent_version_id
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

CREATE OR REPLACE FUNCTION public.state_artifact_tier2_gate(check_version_id UUID)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
WITH version_context AS (
    SELECT
        v.version_id,
        v.artifact_id,
        a.artifact_kind
    FROM public.artifact_versions v
    JOIN public.artifacts a ON a.artifact_id = v.artifact_id
    WHERE v.version_id = check_version_id
),
required_audits AS (
    SELECT unnest(ARRAY[
        'asset_integrity_check',
        'format_security_scan',
        'loader_policy_check',
        'golden_eval',
        'determinism_replay'
    ]::TEXT[]) AS audit_type
),
checks AS (
    SELECT 'version_exists'::TEXT AS check_name, EXISTS (
        SELECT 1 FROM version_context
    ) AS passed
    UNION ALL
    SELECT 'is_state_artifact', EXISTS (
        SELECT 1 FROM version_context WHERE artifact_kind = 'state_artifact'
    )
    UNION ALL
    SELECT 'has_assets', EXISTS (
        SELECT 1
        FROM public.artifact_assets aa
        WHERE aa.version_id = check_version_id
    )
    UNION ALL
    SELECT 'has_state_metadata', EXISTS (
        SELECT 1
        FROM public.state_artifact_metadata sam
        WHERE sam.version_id = check_version_id
    )
    UNION ALL
    SELECT
        'audit_' || required_audits.audit_type,
        EXISTS (
            SELECT 1
            FROM version_context vc
            JOIN public.artifact_audit_evidence e
              ON e.artifact_id = vc.artifact_id
             AND e.version_id = vc.version_id
            WHERE e.audit_type = required_audits.audit_type
              AND e.passed = TRUE
              AND e.status = 'completed'
        )
    FROM required_audits
),
failed_checks AS (
    SELECT check_name
    FROM checks
    WHERE passed = FALSE
)
SELECT jsonb_build_object(
    'version_id', check_version_id,
    'artifact_id', (SELECT artifact_id FROM version_context),
    'passed', NOT EXISTS (SELECT 1 FROM failed_checks),
    'blockers', COALESCE(
        (SELECT jsonb_agg(check_name ORDER BY check_name) FROM failed_checks),
        '[]'::jsonb
    )
);
$$;

GRANT EXECUTE ON FUNCTION public.state_artifact_tier2_gate(UUID) TO authenticated;
