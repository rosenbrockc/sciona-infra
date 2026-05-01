-- Physics CDG artifact envelope publication readiness.
-- Additive read-only surfaces for matcher publication clients that need a
-- version-scoped CDG envelope plus symbolic review/replay state. Existing
-- table shape, triggers, policies, and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_cdg_artifact_envelope_publication AS
WITH version_scope AS (
    SELECT
        a.artifact_id,
        a.fqdn,
        a.status AS artifact_status,
        a.visibility_tier,
        a.is_publishable AS materialized_is_publishable,
        v.version_id,
        v.content_hash,
        v.semver,
        v.is_latest,
        COALESCE(ar.overall_verdict, 'unknown') AS overall_verdict,
        COALESCE(ar.review_status, 'missing') AS artifact_review_status,
        COALESCE(ar.trust_readiness, 'not_ready') AS trust_readiness,
        COALESCE(ar.trust_blockers, '{}'::TEXT[]) AS trust_blockers
    FROM public.artifacts a
    JOIN public.artifact_versions v
      ON v.artifact_id = a.artifact_id
    LEFT JOIN public.artifact_audit_rollups ar
      ON ar.artifact_id = a.artifact_id
    WHERE a.artifact_kind = 'cdg'
),
cdg_rollup AS (
    SELECT
        vs.version_id,
        COUNT(DISTINCT n.node_id) AS cdg_node_count,
        COUNT(DISTINCT (e.source_id, e.target_id, e.output_name, e.input_name)) FILTER (
            WHERE e.source_id IS NOT NULL
              AND e.target_id IS NOT NULL
        ) AS cdg_edge_count,
        COUNT(DISTINCT b.binding_id) AS cdg_binding_count,
        COUNT(DISTINCT b.binding_id) FILTER (
            WHERE b.bound_artifact_fqdn <> ''
              AND b.bound_version_content_hash <> ''
        ) AS replay_keyed_binding_count
    FROM version_scope vs
    LEFT JOIN public.artifact_cdg_nodes n
      ON n.version_id = vs.version_id
    LEFT JOIN public.artifact_cdg_edges e
      ON e.version_id = vs.version_id
    LEFT JOIN public.artifact_cdg_bindings b
      ON b.version_id = vs.version_id
    GROUP BY vs.version_id
),
symbolic_rollup AS (
    SELECT
        se.version_id,
        COUNT(*) AS symbolic_expression_count,
        COUNT(*) FILTER (
            WHERE se.review_status IN ('automated_pass', 'human_reviewed')
        ) AS reviewed_symbolic_expression_count,
        COUNT(*) FILTER (
            WHERE se.review_status = 'needs_human'
        ) AS needs_human_review_expression_count,
        COUNT(*) FILTER (
            WHERE se.review_status = 'blocked'
        ) AS blocked_symbolic_expression_count,
        COUNT(*) FILTER (
            WHERE se.source_expression_id <> ''
              AND se.canonical_expr_hash <> ''
              AND se.topology_hash <> ''
              AND se.dimensional_hash <> ''
        ) AS replay_ready_symbolic_expression_count,
        COUNT(*) FILTER (
            WHERE se.validation_status = 'passed'
        ) AS validation_passed_expression_count
    FROM public.artifact_symbolic_expressions se
    GROUP BY se.version_id
)
SELECT
    vs.artifact_id,
    vs.fqdn,
    vs.artifact_status,
    vs.visibility_tier,
    vs.version_id,
    vs.content_hash,
    vs.semver,
    vs.is_latest,
    vs.materialized_is_publishable,
    public.artifact_is_publishable(vs.artifact_id) AS computed_is_publishable,
    vs.overall_verdict,
    vs.artifact_review_status,
    vs.trust_readiness,
    vs.trust_blockers,
    COALESCE(cr.cdg_node_count, 0) AS cdg_node_count,
    COALESCE(cr.cdg_edge_count, 0) AS cdg_edge_count,
    COALESCE(cr.cdg_binding_count, 0) AS cdg_binding_count,
    COALESCE(cr.replay_keyed_binding_count, 0) AS replay_keyed_binding_count,
    COALESCE(sr.symbolic_expression_count, 0) AS symbolic_expression_count,
    COALESCE(sr.reviewed_symbolic_expression_count, 0) AS reviewed_symbolic_expression_count,
    COALESCE(sr.needs_human_review_expression_count, 0) AS needs_human_review_expression_count,
    COALESCE(sr.blocked_symbolic_expression_count, 0) AS blocked_symbolic_expression_count,
    COALESCE(sr.replay_ready_symbolic_expression_count, 0) AS replay_ready_symbolic_expression_count,
    COALESCE(sr.validation_passed_expression_count, 0) AS validation_passed_expression_count,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN vs.artifact_status <> 'approved' THEN 'artifact_not_approved' END,
        CASE WHEN vs.content_hash = '' THEN 'missing_version_content_hash' END,
        CASE WHEN vs.semver = '' THEN 'missing_version_semver' END,
        CASE WHEN COALESCE(cr.cdg_node_count, 0) = 0 THEN 'missing_cdg_nodes' END,
        CASE WHEN COALESCE(cr.cdg_edge_count, 0) = 0 THEN 'missing_cdg_edges' END,
        CASE WHEN COALESCE(cr.cdg_binding_count, 0) = 0 THEN 'missing_cdg_bindings' END,
        CASE WHEN COALESCE(cr.cdg_binding_count, 0) <> COALESCE(cr.replay_keyed_binding_count, 0) THEN 'cdg_binding_replay_keys_incomplete' END,
        CASE WHEN vs.artifact_review_status <> 'approved' THEN 'artifact_review_status_not_approved' END,
        CASE WHEN vs.overall_verdict IN ('broken', 'misleading') THEN 'audit_verdict_blocks_publication' END,
        CASE WHEN vs.trust_readiness NOT IN (
            'reviewed_with_limits',
            'catalog_ready',
            'ready_for_manifest_merge',
            'ready'
        ) THEN 'trust_readiness_not_publication_ready' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.reviewed_symbolic_expression_count, 0)
             THEN 'symbolic_review_status_update_needed' END,
        CASE WHEN COALESCE(sr.blocked_symbolic_expression_count, 0) > 0 THEN 'blocked_symbolic_expression' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.replay_ready_symbolic_expression_count, 0)
             THEN 'symbolic_replay_keys_incomplete' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.validation_passed_expression_count, 0)
             THEN 'symbolic_validation_not_passed' END,
        CASE WHEN NOT public.artifact_is_publishable(vs.artifact_id) THEN 'computed_not_publishable' END,
        CASE WHEN vs.materialized_is_publishable <> public.artifact_is_publishable(vs.artifact_id) THEN 'publishable_materialization_mismatch' END
    ], NULL) AS publication_blockers,
    CARDINALITY(ARRAY_REMOVE(ARRAY[
        CASE WHEN vs.artifact_status <> 'approved' THEN 'artifact_not_approved' END,
        CASE WHEN vs.content_hash = '' THEN 'missing_version_content_hash' END,
        CASE WHEN vs.semver = '' THEN 'missing_version_semver' END,
        CASE WHEN COALESCE(cr.cdg_node_count, 0) = 0 THEN 'missing_cdg_nodes' END,
        CASE WHEN COALESCE(cr.cdg_edge_count, 0) = 0 THEN 'missing_cdg_edges' END,
        CASE WHEN COALESCE(cr.cdg_binding_count, 0) = 0 THEN 'missing_cdg_bindings' END,
        CASE WHEN COALESCE(cr.cdg_binding_count, 0) <> COALESCE(cr.replay_keyed_binding_count, 0) THEN 'cdg_binding_replay_keys_incomplete' END,
        CASE WHEN vs.artifact_review_status <> 'approved' THEN 'artifact_review_status_not_approved' END,
        CASE WHEN vs.overall_verdict IN ('broken', 'misleading') THEN 'audit_verdict_blocks_publication' END,
        CASE WHEN vs.trust_readiness NOT IN (
            'reviewed_with_limits',
            'catalog_ready',
            'ready_for_manifest_merge',
            'ready'
        ) THEN 'trust_readiness_not_publication_ready' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.reviewed_symbolic_expression_count, 0)
             THEN 'symbolic_review_status_update_needed' END,
        CASE WHEN COALESCE(sr.blocked_symbolic_expression_count, 0) > 0 THEN 'blocked_symbolic_expression' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.replay_ready_symbolic_expression_count, 0)
             THEN 'symbolic_replay_keys_incomplete' END,
        CASE WHEN COALESCE(sr.symbolic_expression_count, 0) > 0
              AND COALESCE(sr.symbolic_expression_count, 0) <> COALESCE(sr.validation_passed_expression_count, 0)
             THEN 'symbolic_validation_not_passed' END,
        CASE WHEN NOT public.artifact_is_publishable(vs.artifact_id) THEN 'computed_not_publishable' END,
        CASE WHEN vs.materialized_is_publishable <> public.artifact_is_publishable(vs.artifact_id) THEN 'publishable_materialization_mismatch' END
    ], NULL)) = 0 AS publication_ready
FROM version_scope vs
LEFT JOIN cdg_rollup cr
  ON cr.version_id = vs.version_id
LEFT JOIN symbolic_rollup sr
  ON sr.version_id = vs.version_id;

CREATE OR REPLACE FUNCTION public.physics_cdg_artifact_envelope_publication(
    request_fqdn TEXT DEFAULT NULL,
    request_latest_only BOOLEAN DEFAULT TRUE
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'cdg_artifact_envelopes', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.publication_ready DESC,
                    row_data.fqdn,
                    row_data.is_latest DESC,
                    row_data.semver,
                    row_data.version_id
            ),
            '[]'::jsonb
        )
        FROM public.physics_cdg_artifact_envelope_publication row_data
        WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
          AND (NOT request_latest_only OR row_data.is_latest)
    ),
    'summary', jsonb_build_object(
        'cdg_version_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_artifact_envelope_publication row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (NOT request_latest_only OR row_data.is_latest)
        ),
        'publication_ready_version_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_artifact_envelope_publication row_data
            WHERE row_data.publication_ready
              AND (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (NOT request_latest_only OR row_data.is_latest)
        ),
        'review_status_update_needed_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_artifact_envelope_publication row_data
            WHERE 'symbolic_review_status_update_needed' = ANY(row_data.publication_blockers)
              AND (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (NOT request_latest_only OR row_data.is_latest)
        ),
        'replay_key_incomplete_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_artifact_envelope_publication row_data
            WHERE (
                    'symbolic_replay_keys_incomplete' = ANY(row_data.publication_blockers)
                 OR 'cdg_binding_replay_keys_incomplete' = ANY(row_data.publication_blockers)
              )
              AND (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (NOT request_latest_only OR row_data.is_latest)
        )
    )
);
$$;

GRANT SELECT ON public.physics_cdg_artifact_envelope_publication TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_cdg_artifact_envelope_publication(TEXT, BOOLEAN) TO authenticated;
