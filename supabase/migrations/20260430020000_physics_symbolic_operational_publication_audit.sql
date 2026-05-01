-- Physics symbolic operational publication audit coverage.
-- Additive read-only audit surfaces for loader runs and symbolic publication
-- readiness. Existing table shape, triggers, policies, and write paths are
-- unchanged.

CREATE OR REPLACE VIEW public.physics_symbolic_loader_run_audit AS
WITH candidate_rollup AS (
    SELECT
        c.snapshot_id,
        COUNT(*) AS candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status = 'raw_imported') AS raw_imported_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status = 'parse_failed') AS parse_failed_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status = 'blocked') AS blocked_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status IN (
            'parsed',
            'dimension_resolved',
            'symbolically_validated',
            'source_verified',
            'human_reviewed',
            'published'
        )) AS parsed_or_later_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status IN (
            'dimension_resolved',
            'symbolically_validated',
            'source_verified',
            'human_reviewed',
            'published'
        )) AS dimension_resolved_or_later_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status IN (
            'source_verified',
            'human_reviewed',
            'published'
        )) AS source_verified_or_later_candidate_count,
        COUNT(*) FILTER (WHERE c.candidate_status = 'published') AS published_candidate_count
    FROM public.physics_equation_candidates c
    GROUP BY c.snapshot_id
),
expression_rollup AS (
    SELECT
        c.snapshot_id,
        COUNT(se.expression_id) AS symbolic_expression_count,
        COUNT(se.expression_id) FILTER (
            WHERE se.review_status IN ('automated_pass', 'human_reviewed')
        ) AS reviewed_expression_count,
        COUNT(se.expression_id) FILTER (
            WHERE se.validation_status = 'passed'
        ) AS validation_passed_expression_count,
        COUNT(se.expression_id) FILTER (
            WHERE se.dimensional_hash <> ''
              AND EXISTS (
                    SELECT 1
                    FROM public.artifact_symbolic_variables sv
                    WHERE sv.expression_id = se.expression_id
                )
              AND NOT EXISTS (
                    SELECT 1
                    FROM public.artifact_symbolic_variables sv
                    WHERE sv.expression_id = se.expression_id
                      AND sv.variable_role <> 'intermediate'
                      AND (
                            sv.dim_signature = ''
                         OR sv.dimension_source IN ('', 'unknown')
                      )
                )
        ) AS dimension_ready_expression_count
    FROM public.physics_equation_candidates c
    LEFT JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = c.candidate_id
    GROUP BY c.snapshot_id
)
SELECT
    s.snapshot_id,
    s.source_system,
    s.source_version,
    s.source_uri,
    s.retrieved_at,
    s.adapter_name,
    s.adapter_version,
    s.license_expression,
    s.payload_sha256,
    COALESCE(cr.candidate_count, 0) AS candidate_count,
    COALESCE(cr.raw_imported_candidate_count, 0) AS raw_imported_candidate_count,
    COALESCE(cr.parse_failed_candidate_count, 0) AS parse_failed_candidate_count,
    COALESCE(cr.blocked_candidate_count, 0) AS blocked_candidate_count,
    COALESCE(cr.parsed_or_later_candidate_count, 0) AS parsed_or_later_candidate_count,
    COALESCE(cr.dimension_resolved_or_later_candidate_count, 0) AS dimension_resolved_or_later_candidate_count,
    COALESCE(cr.source_verified_or_later_candidate_count, 0) AS source_verified_or_later_candidate_count,
    COALESCE(cr.published_candidate_count, 0) AS published_candidate_count,
    COALESCE(er.symbolic_expression_count, 0) AS symbolic_expression_count,
    COALESCE(er.reviewed_expression_count, 0) AS reviewed_expression_count,
    COALESCE(er.validation_passed_expression_count, 0) AS validation_passed_expression_count,
    COALESCE(er.dimension_ready_expression_count, 0) AS dimension_ready_expression_count,
    CASE
        WHEN COALESCE(cr.candidate_count, 0) = 0 THEN 'empty_run'
        WHEN COALESCE(cr.parse_failed_candidate_count, 0) > 0
          OR COALESCE(cr.blocked_candidate_count, 0) > 0 THEN 'needs_triage'
        WHEN COALESCE(cr.published_candidate_count, 0) = COALESCE(cr.candidate_count, 0) THEN 'fully_published'
        WHEN COALESCE(er.dimension_ready_expression_count, 0) > 0 THEN 'partially_ready'
        ELSE 'ingested'
    END AS operational_status
FROM public.physics_ingest_snapshots s
LEFT JOIN candidate_rollup cr
  ON cr.snapshot_id = s.snapshot_id
LEFT JOIN expression_rollup er
  ON er.snapshot_id = s.snapshot_id;

CREATE OR REPLACE VIEW public.physics_symbolic_publication_readiness AS
WITH expression_checks AS (
    SELECT
        se.expression_id,
        se.artifact_id,
        se.version_id,
        se.candidate_id,
        se.parse_status,
        se.review_status,
        se.validation_status,
        se.dimensional_hash,
        EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
        ) AS has_symbolic_variables,
        EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
              AND sv.variable_role <> 'intermediate'
              AND sv.dim_signature = ''
        ) AS has_missing_variable_dimensions,
        EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
              AND sv.variable_role <> 'intermediate'
              AND sv.dim_signature <> ''
              AND sv.dimension_source IN ('', 'unknown')
        ) AS has_unknown_dimension_source,
        EXISTS (
            SELECT 1
            FROM public.artifact_validity_bounds vb
            WHERE vb.expression_id = se.expression_id
              AND vb.review_status IN ('automated_pass', 'human_reviewed')
        ) AS has_reviewed_validity_bounds,
        EXISTS (
            SELECT 1
            FROM public.artifact_relationships rel
            WHERE rel.verified = TRUE
              AND (
                    rel.source_expression_id = se.expression_id
                 OR rel.target_expression_id = se.expression_id
              )
        ) AS has_verified_relationship
    FROM public.artifact_symbolic_expressions se
),
publication_checks AS (
    SELECT
        ec.*,
        a.artifact_kind,
        a.fqdn,
        a.status AS artifact_status,
        a.is_publishable AS materialized_is_publishable,
        public.artifact_is_publishable(ec.artifact_id) AS computed_is_publishable,
        v.semver,
        s.source_system,
        s.source_version,
        c.source_candidate_id,
        EXISTS (
            SELECT 1
            FROM public.artifact_descriptions d
            WHERE d.artifact_id = ec.artifact_id
              AND d.kind = 'dejargonized'
              AND d.language = 'en'
        ) AS has_dejargonized_description,
        EXISTS (
            SELECT 1
            FROM public.artifact_references r
            WHERE r.artifact_id = ec.artifact_id
        ) AS has_reference,
        EXISTS (
            SELECT 1
            FROM public.artifact_audit_rollups ar
            WHERE ar.artifact_id = ec.artifact_id
              AND ar.overall_verdict NOT IN ('broken', 'misleading')
              AND ar.trust_readiness IN (
                  'reviewed_with_limits',
                  'catalog_ready',
                  'ready_for_manifest_merge',
                  'ready'
              )
        ) AS has_publication_audit_rollup
    FROM expression_checks ec
    JOIN public.artifacts a
      ON a.artifact_id = ec.artifact_id
    JOIN public.artifact_versions v
      ON v.version_id = ec.version_id
    LEFT JOIN public.physics_equation_candidates c
      ON c.candidate_id = ec.candidate_id
    LEFT JOIN public.physics_ingest_snapshots s
      ON s.snapshot_id = c.snapshot_id
)
SELECT
    pc.artifact_id,
    pc.artifact_kind,
    pc.fqdn,
    pc.artifact_status,
    pc.version_id,
    pc.semver,
    pc.expression_id,
    pc.source_system,
    pc.source_version,
    pc.source_candidate_id,
    pc.parse_status,
    pc.review_status,
    pc.validation_status,
    pc.materialized_is_publishable,
    pc.computed_is_publishable,
    pc.has_dejargonized_description,
    pc.has_reference,
    pc.has_publication_audit_rollup,
    pc.has_symbolic_variables,
    pc.dimensional_hash <> '' AS has_dimensional_hash,
    NOT pc.has_missing_variable_dimensions AS has_complete_variable_dimensions,
    NOT pc.has_unknown_dimension_source AS has_known_dimension_sources,
    pc.has_reviewed_validity_bounds,
    pc.has_verified_relationship,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN pc.artifact_status <> 'approved' THEN 'artifact_not_approved' END,
        CASE WHEN pc.parse_status NOT IN ('normalized', 'parsed') THEN 'expression_not_parsed' END,
        CASE WHEN pc.review_status NOT IN ('automated_pass', 'human_reviewed') THEN 'expression_not_reviewed' END,
        CASE WHEN pc.validation_status <> 'passed' THEN 'expression_not_validated' END,
        CASE WHEN pc.dimensional_hash = '' THEN 'missing_dimensional_hash' END,
        CASE WHEN NOT pc.has_symbolic_variables THEN 'missing_symbolic_variables' END,
        CASE WHEN pc.has_missing_variable_dimensions THEN 'missing_variable_dimensions' END,
        CASE WHEN pc.has_unknown_dimension_source THEN 'unknown_dimension_source' END,
        CASE WHEN NOT pc.has_dejargonized_description THEN 'missing_dejargonized_description' END,
        CASE WHEN NOT pc.has_reference THEN 'missing_reference' END,
        CASE WHEN NOT pc.has_publication_audit_rollup THEN 'missing_publication_audit_rollup' END,
        CASE WHEN NOT pc.computed_is_publishable THEN 'computed_not_publishable' END,
        CASE WHEN pc.materialized_is_publishable <> pc.computed_is_publishable THEN 'publishable_materialization_mismatch' END
    ], NULL) AS readiness_blockers,
    CASE
        WHEN CARDINALITY(ARRAY_REMOVE(ARRAY[
            CASE WHEN pc.artifact_status <> 'approved' THEN 'artifact_not_approved' END,
            CASE WHEN pc.parse_status NOT IN ('normalized', 'parsed') THEN 'expression_not_parsed' END,
            CASE WHEN pc.review_status NOT IN ('automated_pass', 'human_reviewed') THEN 'expression_not_reviewed' END,
            CASE WHEN pc.validation_status <> 'passed' THEN 'expression_not_validated' END,
            CASE WHEN pc.dimensional_hash = '' THEN 'missing_dimensional_hash' END,
            CASE WHEN NOT pc.has_symbolic_variables THEN 'missing_symbolic_variables' END,
            CASE WHEN pc.has_missing_variable_dimensions THEN 'missing_variable_dimensions' END,
            CASE WHEN pc.has_unknown_dimension_source THEN 'unknown_dimension_source' END,
            CASE WHEN NOT pc.has_dejargonized_description THEN 'missing_dejargonized_description' END,
            CASE WHEN NOT pc.has_reference THEN 'missing_reference' END,
            CASE WHEN NOT pc.has_publication_audit_rollup THEN 'missing_publication_audit_rollup' END,
            CASE WHEN NOT pc.computed_is_publishable THEN 'computed_not_publishable' END,
            CASE WHEN pc.materialized_is_publishable <> pc.computed_is_publishable THEN 'publishable_materialization_mismatch' END
        ], NULL)) = 0 THEN 'publication_ready'
        ELSE 'blocked'
    END AS readiness_status
FROM publication_checks pc;

CREATE OR REPLACE FUNCTION public.physics_symbolic_operational_publication_audit()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'loader_runs', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY row_data.retrieved_at DESC, row_data.source_system, row_data.source_version
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_loader_run_audit row_data
    ),
    'publication_readiness', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY row_data.fqdn, row_data.semver, row_data.expression_id
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_publication_readiness row_data
    ),
    'summary', jsonb_build_object(
        'loader_run_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_loader_run_audit
        ),
        'needs_triage_loader_run_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_loader_run_audit
            WHERE operational_status = 'needs_triage'
        ),
        'symbolic_expression_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_publication_readiness
        ),
        'publication_ready_expression_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_publication_readiness
            WHERE readiness_status = 'publication_ready'
        ),
        'publishable_materialization_mismatch_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_publication_readiness
            WHERE 'publishable_materialization_mismatch' = ANY(readiness_blockers)
        )
    )
);
$$;

GRANT SELECT ON public.physics_symbolic_loader_run_audit TO authenticated;
GRANT SELECT ON public.physics_symbolic_publication_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_symbolic_operational_publication_audit() TO authenticated;
