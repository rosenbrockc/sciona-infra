-- Physics backfill review/publication observability.
-- Additive read-only status rows for replaying matcher review-status patches,
-- normalized symbolic publication rows, and CDG publication envelopes. Existing
-- table shape, triggers, policies, indexes, and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_backfill_review_publication_status_rows AS
WITH symbolic_rows AS (
    SELECT
        COALESCE(source_system, '') AS source_system,
        COALESCE(source_version, '') AS source_version,
        COALESCE(source_family, '') AS source_family,
        expression_id,
        version_id,
        source_expression_id,
        expression_role,
        parse_status,
        review_status,
        validation_status,
        canonical_expr_hash,
        topology_hash,
        dimensional_hash,
        replay_ready,
        retrieval_ready
    FROM public.physics_symbolic_retrieval_rows
),
symbolic_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(*) AS source_row_count,
        COUNT(*) FILTER (
            WHERE version_id IS NOT NULL
              AND expression_role <> ''
              AND source_expression_id <> ''
        ) AS conflict_keyed_row_count,
        COUNT(*) FILTER (
            WHERE parse_status = 'normalized'
        ) AS normalized_row_count,
        COUNT(*) FILTER (
            WHERE parse_status = 'normalized'
              AND replay_ready
        ) AS normalized_replay_ready_row_count,
        COUNT(*) FILTER (
            WHERE parse_status IN ('parsed', 'normalized')
              AND validation_status = 'passed'
              AND source_expression_id <> ''
        ) AS review_patch_ready_row_count,
        COUNT(*) FILTER (
            WHERE review_status IN ('automated_pass', 'human_reviewed')
        ) AS review_patch_applied_row_count,
        COUNT(*) FILTER (
            WHERE parse_status IN ('parsed', 'normalized')
              AND validation_status = 'passed'
              AND source_expression_id <> ''
              AND review_status NOT IN ('automated_pass', 'human_reviewed')
        ) AS review_patch_pending_row_count,
        COUNT(*) FILTER (
            WHERE retrieval_ready
        ) AS publication_ready_row_count,
        COUNT(*) FILTER (
            WHERE NOT retrieval_ready
        ) AS blocked_row_count
    FROM symbolic_rows
    GROUP BY source_system, source_version, source_family
),
cdg_version_scope AS (
    SELECT DISTINCT
        sr.source_system,
        sr.source_version,
        sr.source_family,
        ce.artifact_id,
        ce.version_id,
        ce.content_hash,
        ce.semver,
        ce.publication_ready,
        ce.publication_blockers
    FROM symbolic_rows sr
    JOIN public.physics_cdg_artifact_envelope_publication ce
      ON ce.version_id = sr.version_id
),
cdg_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(*) AS source_row_count,
        COUNT(*) FILTER (
            WHERE artifact_id IS NOT NULL
              AND content_hash <> ''
        ) AS conflict_keyed_row_count,
        COUNT(*) FILTER (
            WHERE content_hash <> ''
              AND semver <> ''
        ) AS normalized_row_count,
        COUNT(*) FILTER (
            WHERE content_hash <> ''
              AND semver <> ''
              AND NOT (
                    'symbolic_replay_keys_incomplete' = ANY(publication_blockers)
                 OR 'cdg_binding_replay_keys_incomplete' = ANY(publication_blockers)
              )
        ) AS normalized_replay_ready_row_count,
        0::BIGINT AS review_patch_ready_row_count,
        0::BIGINT AS review_patch_applied_row_count,
        0::BIGINT AS review_patch_pending_row_count,
        COUNT(*) FILTER (
            WHERE publication_ready
        ) AS publication_ready_row_count,
        COUNT(*) FILTER (
            WHERE NOT publication_ready
        ) AS blocked_row_count
    FROM cdg_version_scope
    GROUP BY source_system, source_version, source_family
)
SELECT
    source_system,
    source_version,
    source_family,
    'normalized_publication_rows'::TEXT AS status_family,
    'public.artifact_symbolic_expressions'::TEXT AS source_table,
    'version_id,expression_role,source_expression_id'::TEXT AS conflict_key,
    source_row_count,
    conflict_keyed_row_count,
    normalized_row_count,
    normalized_replay_ready_row_count,
    0::BIGINT AS review_patch_ready_row_count,
    0::BIGINT AS review_patch_applied_row_count,
    0::BIGINT AS review_patch_pending_row_count,
    publication_ready_row_count,
    blocked_row_count
FROM symbolic_rollup
UNION ALL
SELECT
    source_system,
    source_version,
    source_family,
    'review_status_patch_rows'::TEXT AS status_family,
    'public.artifact_symbolic_expressions'::TEXT AS source_table,
    'version_id,expression_role,source_expression_id'::TEXT AS conflict_key,
    source_row_count,
    conflict_keyed_row_count,
    normalized_row_count,
    normalized_replay_ready_row_count,
    review_patch_ready_row_count,
    review_patch_applied_row_count,
    review_patch_pending_row_count,
    publication_ready_row_count,
    blocked_row_count
FROM symbolic_rollup
UNION ALL
SELECT
    source_system,
    source_version,
    source_family,
    'cdg_artifact_envelope_rows'::TEXT AS status_family,
    'public.artifact_versions'::TEXT AS source_table,
    'artifact_id,content_hash'::TEXT AS conflict_key,
    source_row_count,
    conflict_keyed_row_count,
    normalized_row_count,
    normalized_replay_ready_row_count,
    review_patch_ready_row_count,
    review_patch_applied_row_count,
    review_patch_pending_row_count,
    publication_ready_row_count,
    blocked_row_count
FROM cdg_rollup;

CREATE OR REPLACE FUNCTION public.physics_backfill_review_publication_observability(
    request_source_family TEXT DEFAULT NULL,
    request_source_table TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'status_rows', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.source_system,
                    row_data.source_version,
                    row_data.source_family,
                    row_data.source_table,
                    row_data.conflict_key,
                    row_data.status_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_backfill_review_publication_status_rows row_data
        WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
          AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
    ),
    'summary', jsonb_build_object(
        'status_row_count', (
            SELECT COUNT(*)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        ),
        'source_row_count', (
            SELECT COALESCE(SUM(source_row_count), 0)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        ),
        'conflict_keyed_row_count', (
            SELECT COALESCE(SUM(conflict_keyed_row_count), 0)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        ),
        'normalized_replay_ready_row_count', (
            SELECT COALESCE(SUM(normalized_replay_ready_row_count), 0)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        ),
        'review_patch_pending_row_count', (
            SELECT COALESCE(SUM(review_patch_pending_row_count), 0)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        ),
        'publication_ready_row_count', (
            SELECT COALESCE(SUM(publication_ready_row_count), 0)
            FROM public.physics_backfill_review_publication_status_rows row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_source_table IS NULL OR row_data.source_table = request_source_table)
        )
    )
);
$$;

GRANT SELECT ON public.physics_backfill_review_publication_status_rows TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_backfill_review_publication_observability(TEXT, TEXT) TO authenticated;
