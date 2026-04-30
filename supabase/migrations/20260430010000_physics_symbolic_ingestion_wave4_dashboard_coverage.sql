-- Physics symbolic ingestion wave 4 dashboard coverage.
-- Additive read-only coverage surfaces over the Wave 0 physics ingestion
-- tables. No existing table shape, trigger, policy, or write path is changed.

CREATE OR REPLACE VIEW public.physics_symbolic_ingestion_source_coverage AS
WITH candidate_source AS (
    SELECT
        s.source_system,
        s.source_version,
        s.snapshot_id,
        c.candidate_id,
        c.candidate_status
    FROM public.physics_ingest_snapshots s
    LEFT JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
),
expression_readiness AS (
    SELECT
        se.expression_id,
        se.candidate_id,
        se.review_status,
        se.validation_status,
        CASE
            WHEN se.dimensional_hash <> ''
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
                THEN TRUE
            ELSE FALSE
        END AS dimension_ready
    FROM public.artifact_symbolic_expressions se
)
SELECT
    cs.source_system,
    cs.source_version,
    COUNT(DISTINCT cs.snapshot_id) AS snapshot_count,
    COUNT(cs.candidate_id) AS candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status = 'raw_imported') AS raw_imported_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN (
        'parsed',
        'dimension_resolved',
        'symbolically_validated',
        'source_verified',
        'human_reviewed',
        'published'
    )) AS parsed_or_later_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN (
        'dimension_resolved',
        'symbolically_validated',
        'source_verified',
        'human_reviewed',
        'published'
    )) AS dimension_resolved_or_later_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN (
        'symbolically_validated',
        'source_verified',
        'human_reviewed',
        'published'
    )) AS validated_or_later_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN (
        'source_verified',
        'human_reviewed',
        'published'
    )) AS source_verified_or_later_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN (
        'human_reviewed',
        'published'
    )) AS human_reviewed_or_later_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status = 'published') AS published_candidate_count,
    COUNT(*) FILTER (WHERE cs.candidate_status IN ('parse_failed', 'blocked')) AS blocked_or_failed_candidate_count,
    COUNT(er.expression_id) AS symbolic_expression_count,
    COUNT(er.expression_id) FILTER (
        WHERE er.review_status IN ('automated_pass', 'human_reviewed')
    ) AS reviewed_expression_count,
    COUNT(er.expression_id) FILTER (
        WHERE er.validation_status = 'passed'
    ) AS validation_passed_expression_count,
    COUNT(er.expression_id) FILTER (
        WHERE er.dimension_ready
    ) AS dimension_ready_expression_count
FROM candidate_source cs
LEFT JOIN expression_readiness er
  ON er.candidate_id = cs.candidate_id
GROUP BY cs.source_system, cs.source_version;

CREATE OR REPLACE VIEW public.physics_symbolic_ingestion_status_coverage AS
WITH expression_status AS (
    SELECT
        se.expression_id,
        se.candidate_id,
        se.parse_status,
        se.review_status,
        se.validation_status,
        CASE
            WHEN se.dimensional_hash = '' THEN 'missing_hash'
            WHEN NOT EXISTS (
                    SELECT 1
                    FROM public.artifact_symbolic_variables sv
                    WHERE sv.expression_id = se.expression_id
                ) THEN 'missing_variables'
            WHEN EXISTS (
                    SELECT 1
                    FROM public.artifact_symbolic_variables sv
                    WHERE sv.expression_id = se.expression_id
                      AND sv.variable_role <> 'intermediate'
                      AND sv.dim_signature = ''
                ) THEN 'missing_variable_dimensions'
            WHEN EXISTS (
                    SELECT 1
                    FROM public.artifact_symbolic_variables sv
                    WHERE sv.expression_id = se.expression_id
                      AND sv.variable_role <> 'intermediate'
                      AND sv.dim_signature <> ''
                      AND sv.dimension_source IN ('', 'unknown')
                ) THEN 'unknown_dimension_source'
            ELSE 'ready'
        END AS dimension_status
    FROM public.artifact_symbolic_expressions se
)
SELECT
    s.source_system,
    s.source_version,
    c.candidate_status,
    COALESCE(es.parse_status, 'not_published') AS parse_status,
    COALESCE(es.review_status, 'not_published') AS review_status,
    COALESCE(es.validation_status, 'not_published') AS validation_status,
    COALESCE(es.dimension_status, 'not_published') AS dimension_status,
    COUNT(DISTINCT c.candidate_id) AS candidate_count,
    COUNT(es.expression_id) AS symbolic_expression_count
FROM public.physics_ingest_snapshots s
JOIN public.physics_equation_candidates c
  ON c.snapshot_id = s.snapshot_id
LEFT JOIN expression_status es
  ON es.candidate_id = c.candidate_id
GROUP BY
    s.source_system,
    s.source_version,
    c.candidate_status,
    COALESCE(es.parse_status, 'not_published'),
    COALESCE(es.review_status, 'not_published'),
    COALESCE(es.validation_status, 'not_published'),
    COALESCE(es.dimension_status, 'not_published');

CREATE OR REPLACE VIEW public.physics_symbolic_relationship_kind_coverage AS
SELECT
    relationship_kind,
    source_kind,
    verified,
    COUNT(*) AS relationship_count,
    COUNT(*) FILTER (
        WHERE source_expression_id IS NOT NULL
          AND target_expression_id IS NOT NULL
    ) AS expression_endpoint_bound_count,
    COUNT(*) FILTER (
        WHERE source_kind = 'physics_derivation_graph'
          AND source_expression_id IS NOT NULL
          AND target_expression_id IS NOT NULL
          AND source_node_id <> ''
          AND target_node_id <> ''
          AND inference_rule_id <> ''
    ) AS pdg_relationship_ready_count,
    COUNT(*) FILTER (
        WHERE source_kind = 'physics_derivation_graph'
          AND relationship_kind IN (
              'derives_from',
              'limit_case_of',
              'approximation_of',
              'algebraic_rearrangement_of'
          )
          AND source_expression_id IS NOT NULL
          AND target_expression_id IS NOT NULL
          AND source_node_id <> ''
          AND target_node_id <> ''
          AND inference_rule_id <> ''
          AND (
              evidence_json->>'operation_kind' IN (
                  'solve',
                  'solve_for',
                  'substitute',
                  'substitution',
                  'limit',
                  'take_limit',
                  'derive',
                  'simplify'
              )
              OR relationship_kind IN (
                  'derives_from',
                  'limit_case_of',
                  'algebraic_rearrangement_of'
              )
          )
    ) AS cdg_candidate_ready_count,
    COUNT(*) FILTER (
        WHERE source_expression_id IS NULL
           OR target_expression_id IS NULL
    ) AS missing_expression_endpoint_count
FROM public.artifact_relationships
GROUP BY relationship_kind, source_kind, verified;

CREATE OR REPLACE VIEW public.physics_symbolic_pdg_cdg_readiness AS
WITH pdg_candidates AS (
    SELECT
        c.candidate_id,
        c.source_candidate_id,
        c.candidate_status
    FROM public.physics_equation_candidates c
    JOIN public.physics_ingest_snapshots s
      ON s.snapshot_id = c.snapshot_id
    WHERE s.source_system = 'physics_derivation_graph'
),
pdg_expression_bindings AS (
    SELECT
        pc.candidate_id,
        pc.source_candidate_id,
        se.expression_id
    FROM pdg_candidates pc
    JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = pc.candidate_id
),
pdg_relationships AS (
    SELECT
        relationship_id,
        relationship_kind,
        source_expression_id,
        target_expression_id,
        source_node_id,
        target_node_id,
        inference_rule_id,
        evidence_json,
        (
            source_expression_id IS NOT NULL
            AND target_expression_id IS NOT NULL
            AND source_node_id <> ''
            AND target_node_id <> ''
            AND inference_rule_id <> ''
        ) AS pdg_ready,
        (
            relationship_kind IN (
                'derives_from',
                'limit_case_of',
                'approximation_of',
                'algebraic_rearrangement_of'
            )
            AND source_expression_id IS NOT NULL
            AND target_expression_id IS NOT NULL
            AND source_node_id <> ''
            AND target_node_id <> ''
            AND inference_rule_id <> ''
            AND (
                evidence_json->>'operation_kind' IN (
                    'solve',
                    'solve_for',
                    'substitute',
                    'substitution',
                    'limit',
                    'take_limit',
                    'derive',
                    'simplify'
                )
                OR relationship_kind IN (
                    'derives_from',
                    'limit_case_of',
                    'algebraic_rearrangement_of'
                )
            )
        ) AS cdg_ready
    FROM public.artifact_relationships
    WHERE source_kind = 'physics_derivation_graph'
)
SELECT
    'physics_derivation_graph'::TEXT AS source_system,
    (SELECT COUNT(*) FROM pdg_candidates) AS pdg_candidate_count,
    (SELECT COUNT(*) FROM pdg_expression_bindings) AS pdg_expression_binding_count,
    (SELECT COUNT(*) FROM pdg_relationships) AS pdg_relationship_count,
    (SELECT COUNT(*) FROM pdg_relationships WHERE pdg_ready) AS pdg_relationship_ready_count,
    (SELECT COUNT(*) FROM pdg_relationships WHERE cdg_ready) AS cdg_candidate_ready_count,
    (SELECT COUNT(*) FROM pdg_relationships WHERE NOT pdg_ready) AS pdg_relationship_blocked_count,
    (SELECT COUNT(*) FROM pdg_candidates WHERE candidate_status = 'published') AS published_pdg_candidate_count;

CREATE OR REPLACE FUNCTION public.physics_symbolic_ingestion_dashboard_coverage()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'source_coverage', (
        SELECT COALESCE(
            jsonb_agg(to_jsonb(row_data) ORDER BY row_data.source_system, row_data.source_version),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_ingestion_source_coverage row_data
    ),
    'status_coverage', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.source_system,
                    row_data.source_version,
                    row_data.candidate_status,
                    row_data.parse_status,
                    row_data.review_status,
                    row_data.validation_status,
                    row_data.dimension_status
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_ingestion_status_coverage row_data
    ),
    'relationship_kind_coverage', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY row_data.relationship_kind, row_data.source_kind, row_data.verified
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_relationship_kind_coverage row_data
    ),
    'pdg_cdg_readiness', (
        SELECT COALESCE(jsonb_agg(to_jsonb(row_data)), '[]'::jsonb)
        FROM public.physics_symbolic_pdg_cdg_readiness row_data
    )
);
$$;

GRANT SELECT ON public.physics_symbolic_ingestion_source_coverage TO authenticated;
GRANT SELECT ON public.physics_symbolic_ingestion_status_coverage TO authenticated;
GRANT SELECT ON public.physics_symbolic_relationship_kind_coverage TO authenticated;
GRANT SELECT ON public.physics_symbolic_pdg_cdg_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_symbolic_ingestion_dashboard_coverage() TO authenticated;
