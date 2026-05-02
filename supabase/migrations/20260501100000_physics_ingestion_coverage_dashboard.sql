-- Physics ingestion Phase 7 coverage dashboard.
-- Additive read-only rollup over persisted source snapshots, equation
-- candidates, symbolic expressions, and publication readiness state. Existing
-- table shape, triggers, policies, indexes, and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_ingestion_coverage_dashboard AS
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
        COALESCE(
            NULLIF(c.source_payload->>'physics_family', ''),
            NULLIF(c.source_payload->>'physics_domain', ''),
            NULLIF(c.source_payload->>'discipline', ''),
            NULLIF(s.payload->>'physics_family', ''),
            NULLIF(s.payload->>'physics_domain', ''),
            NULLIF(s.payload->>'discipline', ''),
            NULLIF(c.mechanism_tags[1], ''),
            s.source_system
        ) AS physics_family,
        c.candidate_id,
        c.source_candidate_id,
        c.candidate_status,
        se.expression_id,
        se.parse_status,
        se.review_status,
        se.validation_status,
        se.dimensional_hash,
        pr.readiness_status AS publication_readiness_status
    FROM public.physics_ingest_snapshots s
    LEFT JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
    LEFT JOIN public.artifact_symbolic_expressions se
      ON se.candidate_id = c.candidate_id
    LEFT JOIN public.physics_symbolic_publication_readiness pr
      ON pr.expression_id = se.expression_id
),
coverage_rollup AS (
    SELECT
        source_system,
        source_family,
        physics_family,
        COUNT(DISTINCT snapshot_id) AS snapshot_count,
        COUNT(DISTINCT candidate_id) AS discovered_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status = 'raw_imported'
        ) AS raw_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN (
                'parsed',
                'dimension_resolved',
                'symbolically_validated',
                'source_verified',
                'human_reviewed',
                'published'
            )
        ) AS parsed_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN (
                'dimension_resolved',
                'symbolically_validated',
                'source_verified',
                'human_reviewed',
                'published'
            )
        ) AS dimensioned_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN (
                'symbolically_validated',
                'source_verified',
                'human_reviewed',
                'published'
            )
        ) AS symbolically_validated_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN ('human_reviewed', 'published')
        ) AS reviewed_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status = 'published'
        ) AS published_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE candidate_status IN ('parse_failed', 'blocked')
        ) AS failed_or_blocked_candidate_count,
        COUNT(DISTINCT candidate_id) FILTER (
            WHERE source_candidate_id <> ''
        ) AS source_candidate_id_count,
        COUNT(DISTINCT expression_id) AS symbolic_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE parse_status IN ('parsed', 'normalized')
        ) AS parsed_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE dimensional_hash <> ''
        ) AS dimensioned_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE validation_status = 'passed'
        ) AS symbolically_validated_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE review_status IN ('automated_pass', 'human_reviewed')
        ) AS reviewed_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE candidate_status = 'published'
        ) AS published_expression_count,
        COUNT(DISTINCT expression_id) FILTER (
            WHERE publication_readiness_status = 'publication_ready'
        ) AS publication_ready_expression_count
    FROM source_rows
    GROUP BY source_system, source_family, physics_family
),
coverage_rows AS (
    SELECT
        cr.*,
        ARRAY_REMOVE(ARRAY[
            CASE WHEN discovered_candidate_count = 0 THEN 'no_discovered_candidates' END,
            CASE WHEN discovered_candidate_count > 0
                   AND parsed_candidate_count <> discovered_candidate_count THEN 'parse_coverage_gap' END,
            CASE WHEN discovered_candidate_count > 0
                   AND dimensioned_candidate_count <> discovered_candidate_count THEN 'dimension_coverage_gap' END,
            CASE WHEN discovered_candidate_count > 0
                   AND symbolically_validated_candidate_count <> discovered_candidate_count THEN 'symbolic_validation_gap' END,
            CASE WHEN discovered_candidate_count > 0
                   AND reviewed_candidate_count <> discovered_candidate_count THEN 'review_coverage_gap' END,
            CASE WHEN discovered_candidate_count > 0
                   AND published_candidate_count <> discovered_candidate_count THEN 'publication_coverage_gap' END,
            CASE WHEN failed_or_blocked_candidate_count > 0 THEN 'failed_or_blocked_candidates' END,
            CASE WHEN discovered_candidate_count > 0
                   AND symbolic_expression_count = 0 THEN 'no_symbolic_expressions' END,
            CASE WHEN symbolic_expression_count > 0
                   AND parsed_expression_count <> symbolic_expression_count THEN 'expression_parse_gap' END,
            CASE WHEN symbolic_expression_count > 0
                   AND dimensioned_expression_count <> symbolic_expression_count THEN 'expression_dimension_gap' END,
            CASE WHEN symbolic_expression_count > 0
                   AND symbolically_validated_expression_count <> symbolic_expression_count THEN 'expression_validation_gap' END,
            CASE WHEN symbolic_expression_count > 0
                   AND reviewed_expression_count <> symbolic_expression_count THEN 'expression_review_gap' END
        ], NULL) AS blockers
    FROM coverage_rollup cr
)
SELECT
    source_system,
    source_family,
    physics_family,
    snapshot_count,
    discovered_candidate_count,
    raw_candidate_count,
    parsed_candidate_count,
    dimensioned_candidate_count,
    symbolically_validated_candidate_count,
    reviewed_candidate_count,
    published_candidate_count,
    failed_or_blocked_candidate_count,
    source_candidate_id_count,
    symbolic_expression_count,
    parsed_expression_count,
    dimensioned_expression_count,
    symbolically_validated_expression_count,
    reviewed_expression_count,
    published_expression_count,
    publication_ready_expression_count,
    blockers,
    CARDINALITY(blockers) AS blocker_count,
    (
        discovered_candidate_count > 0
        AND parsed_candidate_count = discovered_candidate_count
        AND dimensioned_candidate_count = discovered_candidate_count
        AND symbolically_validated_candidate_count = discovered_candidate_count
        AND reviewed_candidate_count = discovered_candidate_count
        AND published_candidate_count = discovered_candidate_count
        AND CARDINALITY(blockers) = 0
    ) AS coverage_ready,
    CASE
        WHEN discovered_candidate_count = 0 THEN 'no_data'
        WHEN failed_or_blocked_candidate_count > 0 THEN 'blocked'
        WHEN published_candidate_count = discovered_candidate_count
          AND CARDINALITY(blockers) = 0 THEN 'published'
        WHEN reviewed_candidate_count = discovered_candidate_count THEN 'reviewed_not_published'
        WHEN symbolically_validated_candidate_count = discovered_candidate_count THEN 'validated_not_reviewed'
        WHEN dimensioned_candidate_count = discovered_candidate_count THEN 'dimensioned_not_validated'
        WHEN parsed_candidate_count = discovered_candidate_count THEN 'parsed_not_dimensioned'
        ELSE 'discovered_not_parsed'
    END AS coverage_status
FROM coverage_rows;

CREATE OR REPLACE FUNCTION public.physics_ingestion_coverage_dashboard(
    request_source_system TEXT DEFAULT NULL,
    request_source_family TEXT DEFAULT NULL,
    request_physics_family TEXT DEFAULT NULL,
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
    'coverage_families', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.coverage_ready DESC,
                    row_data.blocker_count,
                    row_data.source_system,
                    row_data.source_family,
                    row_data.physics_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_ingestion_coverage_dashboard row_data
        WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
          AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
          AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
          AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
          AND (NOT request_ready_only OR row_data.coverage_ready)
    ),
    'summary', jsonb_build_object(
        'coverage_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'coverage_ready_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE row_data.coverage_ready
              AND (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'blocked_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE NOT row_data.coverage_ready
              AND (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'snapshot_count', (
            SELECT COALESCE(SUM(snapshot_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'discovered_candidate_count', (
            SELECT COALESCE(SUM(discovered_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'parsed_candidate_count', (
            SELECT COALESCE(SUM(parsed_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'dimensioned_candidate_count', (
            SELECT COALESCE(SUM(dimensioned_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'symbolically_validated_candidate_count', (
            SELECT COALESCE(SUM(symbolically_validated_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'reviewed_candidate_count', (
            SELECT COALESCE(SUM(reviewed_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'published_candidate_count', (
            SELECT COALESCE(SUM(published_candidate_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'symbolic_expression_count', (
            SELECT COALESCE(SUM(symbolic_expression_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        ),
        'blocker_count', (
            SELECT COALESCE(SUM(blocker_count), 0)
            FROM public.physics_ingestion_coverage_dashboard row_data
            WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
              AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_physics_family IS NULL OR row_data.physics_family = request_physics_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.coverage_ready)
        )
    )
);
$$;

GRANT SELECT ON public.physics_ingestion_coverage_dashboard TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_ingestion_coverage_dashboard(TEXT, TEXT, TEXT, TEXT, BOOLEAN) TO authenticated;
