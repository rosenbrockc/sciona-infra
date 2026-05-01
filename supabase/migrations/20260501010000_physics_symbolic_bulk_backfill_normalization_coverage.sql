-- Physics symbolic bulk backfill and normalization coverage.
-- Additive read-only rollups for Phase 7 source-family coverage, QUDT
-- dimension provenance, normalization status, and deterministic replay
-- readiness. Existing table shape, triggers, policies, and write paths are
-- unchanged.

CREATE OR REPLACE VIEW public.physics_symbolic_bulk_backfill_source_family_coverage AS
WITH candidate_scope AS (
    SELECT
        s.source_system,
        s.source_version,
        COALESCE(
            NULLIF(c.source_payload->>'source_family', ''),
            NULLIF(c.source_payload->>'family', ''),
            NULLIF(s.payload->>'source_family', ''),
            NULLIF(s.payload->>'family', ''),
            s.source_system
        ) AS source_family,
        s.snapshot_id,
        c.candidate_id,
        c.source_candidate_id,
        c.candidate_status
    FROM public.physics_ingest_snapshots s
    LEFT JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
),
expression_scope AS (
    SELECT
        cs.source_system,
        cs.source_version,
        cs.source_family,
        cs.candidate_id,
        se.expression_id,
        se.source_expression_id,
        se.parse_status,
        se.review_status,
        se.validation_status,
        se.canonical_expr_hash,
        se.topology_hash,
        se.dimensional_hash,
        EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
        ) AS has_symbolic_variables,
        NOT EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
              AND sv.variable_role <> 'intermediate'
              AND sv.dim_signature = ''
        ) AS has_complete_variable_dimensions,
        NOT EXISTS (
            SELECT 1
            FROM public.artifact_symbolic_variables sv
            WHERE sv.expression_id = se.expression_id
              AND sv.variable_role <> 'intermediate'
              AND sv.dim_signature <> ''
              AND sv.dimension_source IN ('', 'unknown')
        ) AS has_known_dimension_sources
    FROM candidate_scope cs
    LEFT JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = cs.candidate_id
),
candidate_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(DISTINCT snapshot_id) AS snapshot_count,
        COUNT(candidate_id) AS discovered_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status = 'raw_imported') AS raw_imported_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN (
            'parsed',
            'dimension_resolved',
            'symbolically_validated',
            'source_verified',
            'human_reviewed',
            'published'
        )) AS parsed_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN (
            'dimension_resolved',
            'symbolically_validated',
            'source_verified',
            'human_reviewed',
            'published'
        )) AS dimensioned_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN (
            'human_reviewed',
            'published'
        )) AS reviewed_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status = 'published') AS published_candidate_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN ('parse_failed', 'blocked')) AS failed_or_blocked_candidate_count,
        COUNT(candidate_id) FILTER (WHERE source_candidate_id <> '') AS source_candidate_id_ready_count
    FROM candidate_scope
    GROUP BY source_system, source_version, source_family
),
expression_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(expression_id) AS symbolic_expression_count,
        COUNT(expression_id) FILTER (WHERE parse_status = 'raw_imported') AS raw_imported_expression_count,
        COUNT(expression_id) FILTER (WHERE parse_status IN ('parsed', 'normalized')) AS parsed_expression_count,
        COUNT(expression_id) FILTER (WHERE parse_status = 'normalized') AS normalized_expression_count,
        COUNT(expression_id) FILTER (WHERE parse_status IN ('parse_failed', 'blocked')) AS normalization_failed_or_blocked_expression_count,
        COUNT(expression_id) FILTER (
            WHERE review_status IN ('automated_pass', 'human_reviewed')
        ) AS reviewed_expression_count,
        COUNT(expression_id) FILTER (
            WHERE validation_status = 'passed'
        ) AS validation_passed_expression_count,
        COUNT(expression_id) FILTER (
            WHERE dimensional_hash <> ''
              AND has_symbolic_variables
              AND has_complete_variable_dimensions
              AND has_known_dimension_sources
        ) AS dimension_ready_expression_count,
        COUNT(expression_id) FILTER (
            WHERE source_expression_id <> ''
              AND canonical_expr_hash <> ''
              AND topology_hash <> ''
              AND dimensional_hash <> ''
        ) AS replay_ready_expression_count
    FROM expression_scope
    GROUP BY source_system, source_version, source_family
),
variable_rollup AS (
    SELECT
        cs.source_system,
        cs.source_version,
        cs.source_family,
        COUNT(sv.variable_id) AS symbolic_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dimension_source = 'qudt') AS qudt_dimension_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dimension_source = 'source') AS source_dimension_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dimension_source = 'manual') AS manual_dimension_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dimension_source = 'inferred') AS inferred_dimension_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dimension_source IN ('', 'unknown')) AS unknown_dimension_variable_count,
        COUNT(sv.variable_id) FILTER (WHERE sv.dim_signature = '') AS missing_dimension_signature_variable_count
    FROM candidate_scope cs
    LEFT JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = cs.candidate_id
    LEFT JOIN public.artifact_symbolic_variables sv
      ON sv.expression_id = se.expression_id
    GROUP BY cs.source_system, cs.source_version, cs.source_family
)
SELECT
    cr.source_system,
    cr.source_version,
    cr.source_family,
    cr.snapshot_count,
    cr.discovered_candidate_count,
    cr.raw_imported_candidate_count,
    cr.parsed_candidate_count,
    cr.dimensioned_candidate_count,
    cr.reviewed_candidate_count,
    cr.published_candidate_count,
    cr.failed_or_blocked_candidate_count,
    cr.source_candidate_id_ready_count,
    COALESCE(er.symbolic_expression_count, 0) AS symbolic_expression_count,
    COALESCE(er.raw_imported_expression_count, 0) AS raw_imported_expression_count,
    COALESCE(er.parsed_expression_count, 0) AS parsed_expression_count,
    COALESCE(er.normalized_expression_count, 0) AS normalized_expression_count,
    COALESCE(er.normalization_failed_or_blocked_expression_count, 0) AS normalization_failed_or_blocked_expression_count,
    COALESCE(er.reviewed_expression_count, 0) AS reviewed_expression_count,
    COALESCE(er.validation_passed_expression_count, 0) AS validation_passed_expression_count,
    COALESCE(er.dimension_ready_expression_count, 0) AS dimension_ready_expression_count,
    COALESCE(er.replay_ready_expression_count, 0) AS replay_ready_expression_count,
    COALESCE(vr.symbolic_variable_count, 0) AS symbolic_variable_count,
    COALESCE(vr.qudt_dimension_variable_count, 0) AS qudt_dimension_variable_count,
    COALESCE(vr.source_dimension_variable_count, 0) AS source_dimension_variable_count,
    COALESCE(vr.manual_dimension_variable_count, 0) AS manual_dimension_variable_count,
    COALESCE(vr.inferred_dimension_variable_count, 0) AS inferred_dimension_variable_count,
    COALESCE(vr.unknown_dimension_variable_count, 0) AS unknown_dimension_variable_count,
    COALESCE(vr.missing_dimension_signature_variable_count, 0) AS missing_dimension_signature_variable_count
FROM candidate_rollup cr
LEFT JOIN expression_rollup er
  ON er.source_system = cr.source_system
 AND er.source_version = cr.source_version
 AND er.source_family = cr.source_family
LEFT JOIN variable_rollup vr
  ON vr.source_system = cr.source_system
 AND vr.source_version = cr.source_version
 AND vr.source_family = cr.source_family;

CREATE OR REPLACE FUNCTION public.physics_symbolic_bulk_backfill_normalization_coverage()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'source_family_coverage', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY row_data.source_system, row_data.source_version, row_data.source_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_bulk_backfill_source_family_coverage row_data
    ),
    'summary', jsonb_build_object(
        'source_family_count', (
            SELECT COUNT(*)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        ),
        'discovered_candidate_count', (
            SELECT COALESCE(SUM(discovered_candidate_count), 0)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        ),
        'normalized_expression_count', (
            SELECT COALESCE(SUM(normalized_expression_count), 0)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        ),
        'qudt_dimension_variable_count', (
            SELECT COALESCE(SUM(qudt_dimension_variable_count), 0)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        ),
        'source_dimension_variable_count', (
            SELECT COALESCE(SUM(source_dimension_variable_count), 0)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        ),
        'replay_ready_expression_count', (
            SELECT COALESCE(SUM(replay_ready_expression_count), 0)
            FROM public.physics_symbolic_bulk_backfill_source_family_coverage
        )
    )
);
$$;

GRANT SELECT ON public.physics_symbolic_bulk_backfill_source_family_coverage TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_symbolic_bulk_backfill_normalization_coverage() TO authenticated;
