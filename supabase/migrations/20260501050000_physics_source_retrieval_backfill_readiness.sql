-- Physics source retrieval/backfill readiness observability.
-- Additive read-only source-family rollups for operators deciding which
-- physics source families can be retrieved/backfilled and which are blocked by
-- provenance, license, replay, review, or publication gaps. Existing table
-- shape, triggers, policies, indexes, and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_source_retrieval_backfill_readiness AS
WITH retrieval_rows AS (
    SELECT
        COALESCE(source_system, '') AS source_system,
        COALESCE(source_version, '') AS source_version,
        COALESCE(source_family, '') AS source_family,
        expression_id,
        source_uri,
        license_expression,
        payload_sha256,
        source_candidate_id,
        source_expression_id,
        parse_status,
        review_status,
        validation_status,
        replay_ready,
        retrieval_ready
    FROM public.physics_symbolic_retrieval_rows
),
retrieval_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(expression_id) AS source_row_count,
        COUNT(expression_id) FILTER (WHERE replay_ready) AS replay_ready_row_count,
        COUNT(expression_id) FILTER (WHERE retrieval_ready) AS retrieval_ready_row_count,
        COUNT(expression_id) FILTER (
            WHERE parse_status IN ('parsed', 'normalized')
              AND validation_status = 'passed'
              AND source_expression_id <> ''
              AND review_status NOT IN ('automated_pass', 'human_reviewed')
        ) AS review_pending_row_count,
        COUNT(expression_id) FILTER (
            WHERE source_uri = ''
               OR payload_sha256 = ''
               OR source_candidate_id = ''
               OR source_expression_id = ''
        ) AS provenance_blocked_row_count,
        COUNT(expression_id) FILTER (
            WHERE license_expression = ''
        ) AS license_blocked_row_count,
        COUNT(expression_id) FILTER (
            WHERE NOT replay_ready
        ) AS replay_blocked_row_count,
        COUNT(expression_id) FILTER (
            WHERE NOT retrieval_ready
        ) AS retrieval_blocked_row_count
    FROM retrieval_rows
    GROUP BY source_system, source_version, source_family
),
source_replay_rollup AS (
    SELECT
        COALESCE(source_system, '') AS source_system,
        COALESCE(source_version, '') AS source_version,
        COALESCE(source_family, '') AS source_family,
        SUM(snapshot_count) AS snapshot_count,
        SUM(source_uri_count) AS source_uri_count,
        SUM(license_expression_count) AS license_expression_count,
        SUM(payload_sha256_count) AS payload_sha256_count,
        SUM(raw_suggestion_count) AS raw_suggestion_count,
        SUM(blocked_or_failed_suggestion_count) AS blocked_or_failed_suggestion_count,
        SUM(source_candidate_id_ready_count) AS source_candidate_id_ready_count,
        SUM(symbolic_expression_count) AS symbolic_expression_count,
        SUM(replay_ready_expression_count) AS replay_ready_expression_count,
        SUM(retrieval_ready_expression_count) AS retrieval_ready_expression_count
    FROM public.physics_symbolic_source_retrieval_replay_readiness
    GROUP BY source_system, source_version, source_family
),
publication_rollup AS (
    SELECT
        COALESCE(source_system, '') AS source_system,
        COALESCE(source_version, '') AS source_version,
        COALESCE(source_family, '') AS source_family,
        SUM(review_patch_pending_row_count) FILTER (
            WHERE status_family = 'review_status_patch_rows'
        ) AS publication_review_pending_row_count,
        SUM(publication_ready_row_count) FILTER (
            WHERE status_family IN (
                'normalized_publication_rows',
                'cdg_artifact_envelope_rows'
            )
        ) AS publication_ready_row_count,
        SUM(blocked_row_count) FILTER (
            WHERE status_family IN (
                'normalized_publication_rows',
                'cdg_artifact_envelope_rows'
            )
        ) AS publication_blocked_row_count
    FROM public.physics_backfill_review_publication_status_rows
    GROUP BY source_system, source_version, source_family
),
source_keys AS (
    SELECT source_system, source_version, source_family FROM retrieval_rollup
    UNION
    SELECT source_system, source_version, source_family FROM source_replay_rollup
    UNION
    SELECT source_system, source_version, source_family FROM publication_rollup
)
SELECT
    sk.source_system,
    sk.source_version,
    sk.source_family,
    COALESCE(rr.source_row_count, COALESCE(srr.symbolic_expression_count, 0)) AS source_row_count,
    COALESCE(srr.snapshot_count, 0) AS snapshot_count,
    COALESCE(srr.raw_suggestion_count, 0) AS raw_suggestion_count,
    COALESCE(srr.blocked_or_failed_suggestion_count, 0) AS blocked_or_failed_suggestion_count,
    COALESCE(rr.replay_ready_row_count, COALESCE(srr.replay_ready_expression_count, 0)) AS replay_ready_row_count,
    COALESCE(rr.retrieval_ready_row_count, COALESCE(srr.retrieval_ready_expression_count, 0)) AS retrieval_ready_row_count,
    GREATEST(
        COALESCE(rr.review_pending_row_count, 0),
        COALESCE(pr.publication_review_pending_row_count, 0)
    ) AS review_pending_row_count,
    COALESCE(rr.provenance_blocked_row_count, 0) AS provenance_blocked_row_count,
    COALESCE(rr.license_blocked_row_count, 0) AS license_blocked_row_count,
    COALESCE(rr.replay_blocked_row_count, 0) AS replay_blocked_row_count,
    COALESCE(rr.retrieval_blocked_row_count, 0) AS retrieval_blocked_row_count,
    COALESCE(pr.publication_ready_row_count, 0) AS publication_ready_row_count,
    COALESCE(pr.publication_blocked_row_count, 0) AS publication_blocked_row_count,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN COALESCE(rr.provenance_blocked_row_count, 0) > 0 THEN 'provenance_gap' END,
        CASE WHEN COALESCE(rr.license_blocked_row_count, 0) > 0 THEN 'license_gap' END,
        CASE WHEN COALESCE(rr.replay_blocked_row_count, 0) > 0 THEN 'replay_gap' END,
        CASE WHEN GREATEST(
            COALESCE(rr.review_pending_row_count, 0),
            COALESCE(pr.publication_review_pending_row_count, 0)
        ) > 0 THEN 'review_pending' END,
        CASE WHEN COALESCE(pr.publication_blocked_row_count, 0) > 0 THEN 'publication_blocked' END,
        CASE WHEN COALESCE(srr.blocked_or_failed_suggestion_count, 0) > 0 THEN 'source_suggestions_blocked_or_failed' END
    ], NULL) AS blockers,
    CARDINALITY(ARRAY_REMOVE(ARRAY[
        CASE WHEN COALESCE(rr.provenance_blocked_row_count, 0) > 0 THEN 'provenance_gap' END,
        CASE WHEN COALESCE(rr.license_blocked_row_count, 0) > 0 THEN 'license_gap' END,
        CASE WHEN COALESCE(rr.replay_blocked_row_count, 0) > 0 THEN 'replay_gap' END,
        CASE WHEN GREATEST(
            COALESCE(rr.review_pending_row_count, 0),
            COALESCE(pr.publication_review_pending_row_count, 0)
        ) > 0 THEN 'review_pending' END,
        CASE WHEN COALESCE(pr.publication_blocked_row_count, 0) > 0 THEN 'publication_blocked' END,
        CASE WHEN COALESCE(srr.blocked_or_failed_suggestion_count, 0) > 0 THEN 'source_suggestions_blocked_or_failed' END
    ], NULL)) AS blocker_count,
    (
        COALESCE(rr.source_row_count, COALESCE(srr.symbolic_expression_count, 0)) > 0
        AND COALESCE(rr.source_row_count, COALESCE(srr.symbolic_expression_count, 0)) = COALESCE(rr.replay_ready_row_count, COALESCE(srr.replay_ready_expression_count, 0))
        AND COALESCE(rr.source_row_count, COALESCE(srr.symbolic_expression_count, 0)) = COALESCE(rr.retrieval_ready_row_count, COALESCE(srr.retrieval_ready_expression_count, 0))
        AND CARDINALITY(ARRAY_REMOVE(ARRAY[
            CASE WHEN COALESCE(rr.provenance_blocked_row_count, 0) > 0 THEN 'provenance_gap' END,
            CASE WHEN COALESCE(rr.license_blocked_row_count, 0) > 0 THEN 'license_gap' END,
            CASE WHEN COALESCE(rr.replay_blocked_row_count, 0) > 0 THEN 'replay_gap' END,
            CASE WHEN GREATEST(
                COALESCE(rr.review_pending_row_count, 0),
                COALESCE(pr.publication_review_pending_row_count, 0)
            ) > 0 THEN 'review_pending' END,
            CASE WHEN COALESCE(pr.publication_blocked_row_count, 0) > 0 THEN 'publication_blocked' END,
            CASE WHEN COALESCE(srr.blocked_or_failed_suggestion_count, 0) > 0 THEN 'source_suggestions_blocked_or_failed' END
        ], NULL)) = 0
    ) AS backfill_ready
FROM source_keys sk
LEFT JOIN retrieval_rollup rr
  ON rr.source_system = sk.source_system
 AND rr.source_version = sk.source_version
 AND rr.source_family = sk.source_family
LEFT JOIN source_replay_rollup srr
  ON srr.source_system = sk.source_system
 AND srr.source_version = sk.source_version
 AND srr.source_family = sk.source_family
LEFT JOIN publication_rollup pr
  ON pr.source_system = sk.source_system
 AND pr.source_version = sk.source_version
 AND pr.source_family = sk.source_family;

CREATE OR REPLACE FUNCTION public.physics_source_retrieval_backfill_observability(
    request_source_family TEXT DEFAULT NULL,
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
    'source_readiness', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.backfill_ready DESC,
                    row_data.blocker_count,
                    row_data.source_system,
                    row_data.source_version,
                    row_data.source_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_source_retrieval_backfill_readiness row_data
        WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
          AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
          AND (NOT request_ready_only OR row_data.backfill_ready)
    ),
    'summary', jsonb_build_object(
        'source_family_count', (
            SELECT COUNT(*)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        ),
        'source_row_count', (
            SELECT COALESCE(SUM(source_row_count), 0)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        ),
        'replay_ready_row_count', (
            SELECT COALESCE(SUM(replay_ready_row_count), 0)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        ),
        'retrieval_ready_row_count', (
            SELECT COALESCE(SUM(retrieval_ready_row_count), 0)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        ),
        'review_pending_row_count', (
            SELECT COALESCE(SUM(review_pending_row_count), 0)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        ),
        'blocker_count', (
            SELECT COALESCE(SUM(blocker_count), 0)
            FROM public.physics_source_retrieval_backfill_readiness row_data
            WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_ready_only OR row_data.backfill_ready)
        )
    )
);
$$;

GRANT SELECT ON public.physics_source_retrieval_backfill_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_source_retrieval_backfill_observability(TEXT, TEXT, BOOLEAN) TO authenticated;
