-- Physics source adapter execution validation readiness.
-- Additive read-only rollups over persisted physics ingest snapshots,
-- candidates, and symbolic expression validation state. This intentionally
-- reports actual stored adapter execution state only; expected source-adapter
-- coverage remains outside the database unless a persisted source appears.

CREATE OR REPLACE VIEW public.physics_source_adapter_execution_validation_readiness AS
WITH source_rows AS (
    SELECT
        s.snapshot_id,
        s.source_system,
        s.source_version,
        COALESCE(
            NULLIF(c.source_payload->>'source_family', ''),
            NULLIF(c.source_payload->>'family', ''),
            NULLIF(s.payload->>'source_family', ''),
            NULLIF(s.payload->>'family', ''),
            s.source_system
        ) AS source_family,
        s.source_uri,
        s.adapter_name,
        s.adapter_version,
        s.payload_sha256,
        c.candidate_id,
        c.candidate_status,
        se.expression_id,
        se.parse_status,
        se.validation_status
    FROM public.physics_ingest_snapshots s
    LEFT JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
    LEFT JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = c.candidate_id
),
adapter_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        adapter_name,
        adapter_version,
        COUNT(DISTINCT snapshot_id) AS snapshot_count,
        COUNT(DISTINCT snapshot_id) FILTER (
            WHERE adapter_name <> ''
              AND adapter_version <> ''
        ) AS adapter_metadata_ready_snapshot_count,
        COUNT(DISTINCT snapshot_id) FILTER (
            WHERE source_uri <> ''
              AND payload_sha256 <> ''
        ) AS provenance_ready_snapshot_count,
        COUNT(DISTINCT candidate_id) AS candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN (
                'parsed',
                'dimension_resolved',
                'symbolically_validated',
                'source_verified',
                'human_reviewed',
                'published'
            )
        ) AS executed_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN ('raw_imported', 'parse_failed', 'blocked')
        ) AS execution_blocked_or_pending_candidate_count,
        COUNT(DISTINCT expression_id) AS symbolic_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE parse_status IN ('parsed', 'normalized')
        ) AS parsed_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE validation_status = 'passed'
        ) AS validation_passed_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE validation_status IN ('unknown', 'failed')
        ) AS validation_blocked_expression_count
    FROM source_rows
    GROUP BY
        source_system,
        source_version,
        source_family,
        adapter_name,
        adapter_version
)
SELECT
    source_system,
    source_version,
    source_family,
    adapter_name,
    adapter_version,
    snapshot_count,
    adapter_metadata_ready_snapshot_count,
    provenance_ready_snapshot_count,
    candidate_count,
    executed_candidate_count,
    execution_blocked_or_pending_candidate_count,
    symbolic_expression_count,
    parsed_expression_count,
    validation_passed_expression_count,
    validation_blocked_expression_count,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN adapter_metadata_ready_snapshot_count <> snapshot_count THEN 'adapter_metadata_gap' END,
        CASE WHEN provenance_ready_snapshot_count <> snapshot_count THEN 'source_provenance_gap' END,
        CASE WHEN candidate_count = 0 THEN 'empty_adapter_run' END,
        CASE WHEN execution_blocked_or_pending_candidate_count > 0
               OR executed_candidate_count <> candidate_count THEN 'source_execution_gap' END,
        CASE WHEN symbolic_expression_count = 0 THEN 'no_symbolic_expressions' END,
        CASE WHEN symbolic_expression_count > 0
               AND parsed_expression_count <> symbolic_expression_count THEN 'expression_parse_gap' END,
        CASE WHEN symbolic_expression_count > 0
               AND validation_passed_expression_count <> symbolic_expression_count THEN 'expression_validation_gap' END
    ], NULL) AS blockers,
    CARDINALITY(ARRAY_REMOVE(ARRAY[
        CASE WHEN adapter_metadata_ready_snapshot_count <> snapshot_count THEN 'adapter_metadata_gap' END,
        CASE WHEN provenance_ready_snapshot_count <> snapshot_count THEN 'source_provenance_gap' END,
        CASE WHEN candidate_count = 0 THEN 'empty_adapter_run' END,
        CASE WHEN execution_blocked_or_pending_candidate_count > 0
               OR executed_candidate_count <> candidate_count THEN 'source_execution_gap' END,
        CASE WHEN symbolic_expression_count = 0 THEN 'no_symbolic_expressions' END,
        CASE WHEN symbolic_expression_count > 0
               AND parsed_expression_count <> symbolic_expression_count THEN 'expression_parse_gap' END,
        CASE WHEN symbolic_expression_count > 0
               AND validation_passed_expression_count <> symbolic_expression_count THEN 'expression_validation_gap' END
    ], NULL)) AS blocker_count,
    (
        snapshot_count > 0
        AND adapter_metadata_ready_snapshot_count = snapshot_count
        AND provenance_ready_snapshot_count = snapshot_count
        AND candidate_count > 0
        AND executed_candidate_count = candidate_count
        AND execution_blocked_or_pending_candidate_count = 0
        AND symbolic_expression_count > 0
        AND parsed_expression_count = symbolic_expression_count
        AND validation_passed_expression_count = symbolic_expression_count
    ) AS source_execution_validation_ready,
    CASE
        WHEN snapshot_count = 0 THEN 'no_data'
        WHEN adapter_metadata_ready_snapshot_count = snapshot_count
          AND provenance_ready_snapshot_count = snapshot_count
          AND candidate_count > 0
          AND executed_candidate_count = candidate_count
          AND execution_blocked_or_pending_candidate_count = 0
          AND symbolic_expression_count > 0
          AND parsed_expression_count = symbolic_expression_count
          AND validation_passed_expression_count = symbolic_expression_count THEN 'ready'
        ELSE 'blocked'
    END AS readiness_status
FROM adapter_rollup;

CREATE OR REPLACE FUNCTION public.physics_source_adapter_execution_validation_observability(
    request_source_family TEXT DEFAULT NULL,
    request_adapter_name TEXT DEFAULT NULL,
    request_blocker TEXT DEFAULT NULL,
    request_ready_only BOOLEAN DEFAULT FALSE
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'source_adapter_execution_validation', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.source_execution_validation_ready DESC,
                    row_data.blocker_count,
                    row_data.source_system,
                    row_data.source_version,
                    row_data.source_family,
                    row_data.adapter_name,
                    row_data.adapter_version
            ),
            '[]'::jsonb
        )
        FROM public.physics_source_adapter_execution_validation_readiness row_data
        WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
          AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
          AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
          AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
    ),
    'summary', jsonb_build_object(
        'adapter_execution_family_count', (
            SELECT COUNT(*)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'ready_adapter_execution_family_count', (
            SELECT COUNT(*)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE row_data.source_execution_validation_ready
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'snapshot_count', (
            SELECT COALESCE(SUM(snapshot_count), 0)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'candidate_count', (
            SELECT COALESCE(SUM(candidate_count), 0)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'symbolic_expression_count', (
            SELECT COALESCE(SUM(symbolic_expression_count), 0)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'validation_passed_expression_count', (
            SELECT COALESCE(SUM(validation_passed_expression_count), 0)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        ),
        'blocker_count', (
            SELECT COALESCE(SUM(blocker_count), 0)
            FROM public.physics_source_adapter_execution_validation_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_adapter_name IS NULL OR row_data.adapter_name = request_adapter_name)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.source_execution_validation_ready)
        )
    )
);
$$;

GRANT SELECT ON public.physics_source_adapter_execution_validation_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_source_adapter_execution_validation_observability(TEXT, TEXT, TEXT, BOOLEAN) TO authenticated;
