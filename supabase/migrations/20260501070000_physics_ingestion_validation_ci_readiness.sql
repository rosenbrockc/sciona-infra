-- Physics ingestion validation/CI readiness rollup.
-- Additive read-only family-level rollups over existing physics readiness
-- surfaces. Existing table shape, triggers, policies, indexes, and write paths
-- are unchanged.

CREATE OR REPLACE VIEW public.physics_ingestion_validation_ci_readiness AS
WITH source_rollup AS (
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (WHERE backfill_ready) AS ready_count,
        COUNT(*) FILTER (WHERE NOT backfill_ready) AS blocked_count,
        COALESCE(SUM(source_row_count), 0) AS source_row_count,
        COALESCE(SUM(replay_ready_row_count), 0) AS replay_ready_row_count,
        COALESCE(SUM(retrieval_ready_row_count), 0) AS retrieval_ready_row_count,
        COALESCE(SUM(review_pending_row_count), 0) AS review_pending_row_count,
        COALESCE(SUM(provenance_blocked_row_count), 0) AS provenance_blocked_row_count,
        COALESCE(SUM(license_blocked_row_count), 0) AS license_blocked_row_count,
        COALESCE(SUM(replay_blocked_row_count), 0) AS replay_blocked_row_count,
        COALESCE(SUM(retrieval_blocked_row_count), 0) AS retrieval_blocked_row_count,
        COALESCE(SUM(publication_blocked_row_count), 0) AS publication_blocked_row_count
    FROM public.physics_source_retrieval_backfill_readiness
),
source_blockers AS (
    SELECT ARRAY(
        SELECT DISTINCT blocker
        FROM (
            SELECT UNNEST(row_data.blockers) AS blocker
            FROM public.physics_source_retrieval_backfill_readiness row_data
            UNION ALL
            SELECT 'no_source_retrieval_backfill_readiness_data'
            WHERE (SELECT row_count FROM source_rollup) = 0
        ) blocker_rows
        ORDER BY blocker
    ) AS blockers
),
cdg_graph_rollup AS (
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (WHERE graph_ready) AS ready_count,
        COUNT(*) FILTER (WHERE NOT graph_ready) AS blocked_count,
        COALESCE(SUM(node_count), 0) AS node_count,
        COALESCE(SUM(edge_count), 0) AS edge_count,
        COALESCE(SUM(binding_count), 0) AS binding_count,
        COALESCE(SUM(missing_edge_endpoint_count), 0) AS missing_edge_endpoint_count,
        COALESCE(SUM(missing_binding_node_count), 0) AS missing_binding_node_count,
        COALESCE(SUM(duplicate_key_count), 0) AS duplicate_key_count
    FROM public.physics_cdg_graph_readiness
),
cdg_graph_blockers AS (
    SELECT ARRAY(
        SELECT DISTINCT blocker
        FROM (
            SELECT UNNEST(row_data.blockers) AS blocker
            FROM public.physics_cdg_graph_readiness row_data
            UNION ALL
            SELECT 'no_cdg_graph_readiness_data'
            WHERE (SELECT row_count FROM cdg_graph_rollup) = 0
        ) blocker_rows
        ORDER BY blocker
    ) AS blockers
),
symbolic_publication_rollup AS (
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (WHERE readiness_status = 'publication_ready') AS ready_count,
        COUNT(*) FILTER (WHERE readiness_status <> 'publication_ready') AS blocked_count,
        COUNT(*) FILTER (WHERE 'expression_not_reviewed' = ANY(readiness_blockers)) AS expression_not_reviewed_count,
        COUNT(*) FILTER (WHERE 'expression_not_validated' = ANY(readiness_blockers)) AS expression_not_validated_count,
        COUNT(*) FILTER (WHERE 'missing_publication_audit_rollup' = ANY(readiness_blockers)) AS missing_publication_audit_rollup_count,
        COUNT(*) FILTER (WHERE 'publishable_materialization_mismatch' = ANY(readiness_blockers)) AS publishable_materialization_mismatch_count
    FROM public.physics_symbolic_publication_readiness
),
symbolic_publication_blockers AS (
    SELECT ARRAY(
        SELECT DISTINCT blocker
        FROM (
            SELECT UNNEST(row_data.readiness_blockers) AS blocker
            FROM public.physics_symbolic_publication_readiness row_data
            UNION ALL
            SELECT 'no_symbolic_publication_readiness_data'
            WHERE (SELECT row_count FROM symbolic_publication_rollup) = 0
        ) blocker_rows
        ORDER BY blocker
    ) AS blockers
),
backfill_publication_rollup AS (
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (
            WHERE blocked_row_count = 0
              AND review_patch_pending_row_count = 0
        ) AS ready_count,
        COUNT(*) FILTER (
            WHERE blocked_row_count > 0
               OR review_patch_pending_row_count > 0
        ) AS blocked_count,
        COALESCE(SUM(source_row_count), 0) AS source_row_count,
        COALESCE(SUM(conflict_keyed_row_count), 0) AS conflict_keyed_row_count,
        COALESCE(SUM(normalized_row_count), 0) AS normalized_row_count,
        COALESCE(SUM(normalized_replay_ready_row_count), 0) AS normalized_replay_ready_row_count,
        COALESCE(SUM(review_patch_pending_row_count), 0) AS review_patch_pending_row_count,
        COALESCE(SUM(publication_ready_row_count), 0) AS publication_ready_row_count,
        COALESCE(SUM(blocked_row_count), 0) AS publication_blocked_row_count
    FROM public.physics_backfill_review_publication_status_rows
),
backfill_publication_blockers AS (
    SELECT ARRAY(
        SELECT DISTINCT blocker
        FROM (
            SELECT UNNEST(ARRAY_REMOVE(ARRAY[
                CASE WHEN row_data.review_patch_pending_row_count > 0 THEN 'review_patch_pending' END,
                CASE WHEN row_data.blocked_row_count > 0 THEN row_data.status_family || '_blocked' END,
                CASE WHEN row_data.conflict_keyed_row_count <> row_data.source_row_count THEN row_data.status_family || '_conflict_key_gap' END,
                CASE WHEN row_data.normalized_replay_ready_row_count <> row_data.normalized_row_count THEN row_data.status_family || '_replay_gap' END
            ], NULL)) AS blocker
            FROM public.physics_backfill_review_publication_status_rows row_data
            UNION ALL
            SELECT 'no_backfill_publication_observability_data'
            WHERE (SELECT row_count FROM backfill_publication_rollup) = 0
        ) blocker_rows
        ORDER BY blocker
    ) AS blockers
),
cdg_publication_rollup AS (
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (WHERE publication_ready) AS ready_count,
        COUNT(*) FILTER (WHERE NOT publication_ready) AS blocked_count,
        COALESCE(SUM(cdg_node_count), 0) AS cdg_node_count,
        COALESCE(SUM(cdg_edge_count), 0) AS cdg_edge_count,
        COALESCE(SUM(cdg_binding_count), 0) AS cdg_binding_count,
        COALESCE(SUM(symbolic_expression_count), 0) AS symbolic_expression_count,
        COALESCE(SUM(reviewed_symbolic_expression_count), 0) AS reviewed_symbolic_expression_count,
        COALESCE(SUM(validation_passed_expression_count), 0) AS validation_passed_expression_count
    FROM public.physics_cdg_artifact_envelope_publication
),
cdg_publication_blockers AS (
    SELECT ARRAY(
        SELECT DISTINCT blocker
        FROM (
            SELECT UNNEST(row_data.publication_blockers) AS blocker
            FROM public.physics_cdg_artifact_envelope_publication row_data
            UNION ALL
            SELECT 'no_cdg_publication_readiness_data'
            WHERE (SELECT row_count FROM cdg_publication_rollup) = 0
        ) blocker_rows
        ORDER BY blocker
    ) AS blockers
),
readiness_rows AS (
    SELECT
        'source_retrieval_backfill'::TEXT AS readiness_family,
        sr.row_count,
        sr.ready_count,
        sr.blocked_count,
        sb.blockers,
        jsonb_build_object(
            'source_row_count', sr.source_row_count,
            'replay_ready_row_count', sr.replay_ready_row_count,
            'retrieval_ready_row_count', sr.retrieval_ready_row_count,
            'review_pending_row_count', sr.review_pending_row_count,
            'provenance_blocked_row_count', sr.provenance_blocked_row_count,
            'license_blocked_row_count', sr.license_blocked_row_count,
            'replay_blocked_row_count', sr.replay_blocked_row_count,
            'retrieval_blocked_row_count', sr.retrieval_blocked_row_count,
            'publication_blocked_row_count', sr.publication_blocked_row_count
        ) AS readiness_detail
    FROM source_rollup sr
    CROSS JOIN source_blockers sb
    UNION ALL
    SELECT
        'cdg_graph'::TEXT AS readiness_family,
        cgr.row_count,
        cgr.ready_count,
        cgr.blocked_count,
        cgb.blockers,
        jsonb_build_object(
            'node_count', cgr.node_count,
            'edge_count', cgr.edge_count,
            'binding_count', cgr.binding_count,
            'missing_edge_endpoint_count', cgr.missing_edge_endpoint_count,
            'missing_binding_node_count', cgr.missing_binding_node_count,
            'duplicate_key_count', cgr.duplicate_key_count
        ) AS readiness_detail
    FROM cdg_graph_rollup cgr
    CROSS JOIN cdg_graph_blockers cgb
    UNION ALL
    SELECT
        'symbolic_publication'::TEXT AS readiness_family,
        spr.row_count,
        spr.ready_count,
        spr.blocked_count,
        spb.blockers,
        jsonb_build_object(
            'expression_not_reviewed_count', spr.expression_not_reviewed_count,
            'expression_not_validated_count', spr.expression_not_validated_count,
            'missing_publication_audit_rollup_count', spr.missing_publication_audit_rollup_count,
            'publishable_materialization_mismatch_count', spr.publishable_materialization_mismatch_count
        ) AS readiness_detail
    FROM symbolic_publication_rollup spr
    CROSS JOIN symbolic_publication_blockers spb
    UNION ALL
    SELECT
        'backfill_publication_observability'::TEXT AS readiness_family,
        bpr.row_count,
        bpr.ready_count,
        bpr.blocked_count,
        bpb.blockers,
        jsonb_build_object(
            'source_row_count', bpr.source_row_count,
            'conflict_keyed_row_count', bpr.conflict_keyed_row_count,
            'normalized_row_count', bpr.normalized_row_count,
            'normalized_replay_ready_row_count', bpr.normalized_replay_ready_row_count,
            'review_patch_pending_row_count', bpr.review_patch_pending_row_count,
            'publication_ready_row_count', bpr.publication_ready_row_count,
            'publication_blocked_row_count', bpr.publication_blocked_row_count
        ) AS readiness_detail
    FROM backfill_publication_rollup bpr
    CROSS JOIN backfill_publication_blockers bpb
    UNION ALL
    SELECT
        'cdg_publication'::TEXT AS readiness_family,
        cpr.row_count,
        cpr.ready_count,
        cpr.blocked_count,
        cpb.blockers,
        jsonb_build_object(
            'cdg_node_count', cpr.cdg_node_count,
            'cdg_edge_count', cpr.cdg_edge_count,
            'cdg_binding_count', cpr.cdg_binding_count,
            'symbolic_expression_count', cpr.symbolic_expression_count,
            'reviewed_symbolic_expression_count', cpr.reviewed_symbolic_expression_count,
            'validation_passed_expression_count', cpr.validation_passed_expression_count
        ) AS readiness_detail
    FROM cdg_publication_rollup cpr
    CROSS JOIN cdg_publication_blockers cpb
)
SELECT
    readiness_family,
    row_count,
    ready_count,
    blocked_count,
    blockers,
    CARDINALITY(blockers) AS blocker_count,
    (
        row_count > 0
        AND ready_count = row_count
        AND blocked_count = 0
        AND CARDINALITY(blockers) = 0
    ) AS validation_ci_ready,
    CASE
        WHEN row_count = 0 THEN 'no_data'
        WHEN blocked_count = 0
          AND ready_count = row_count
          AND CARDINALITY(blockers) = 0 THEN 'ready'
        ELSE 'blocked'
    END AS readiness_status,
    readiness_detail
FROM readiness_rows;

CREATE OR REPLACE FUNCTION public.physics_ingestion_validation_ci_readiness(
    request_readiness_family TEXT DEFAULT NULL,
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
    'readiness_families', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.validation_ci_ready DESC,
                    row_data.blocker_count,
                    row_data.readiness_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_ingestion_validation_ci_readiness row_data
        WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
          AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
          AND (NOT request_ready_only OR row_data.validation_ci_ready)
    ),
    'summary', jsonb_build_object(
        'readiness_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'validation_ci_ready_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE row_data.validation_ci_ready
              AND (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'blocked_family_count', (
            SELECT COUNT(*)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE NOT row_data.validation_ci_ready
              AND (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'row_count', (
            SELECT COALESCE(SUM(row_count), 0)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'ready_count', (
            SELECT COALESCE(SUM(ready_count), 0)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'blocked_count', (
            SELECT COALESCE(SUM(blocked_count), 0)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'blocker_count', (
            SELECT COALESCE(SUM(blocker_count), 0)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        ),
        'validation_ci_ready', (
            SELECT COALESCE(BOOL_AND(validation_ci_ready), FALSE)
            FROM public.physics_ingestion_validation_ci_readiness row_data
            WHERE (request_readiness_family IS NULL OR row_data.readiness_family = request_readiness_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.validation_ci_ready)
        )
    )
);
$$;

GRANT SELECT ON public.physics_ingestion_validation_ci_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_ingestion_validation_ci_readiness(TEXT, TEXT, BOOLEAN) TO authenticated;
