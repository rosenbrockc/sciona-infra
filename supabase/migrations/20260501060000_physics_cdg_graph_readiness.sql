-- Physics CDG graph consistency/readiness.
-- Additive read-only rollups for version-scoped CDG node/edge/binding graph
-- integrity. Existing table shape, constraints, triggers, policies, indexes,
-- and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_cdg_graph_readiness AS
WITH version_scope AS (
    SELECT
        a.artifact_id,
        a.fqdn,
        a.status AS artifact_status,
        v.version_id,
        v.content_hash,
        v.semver,
        v.is_latest
    FROM public.artifacts a
    JOIN public.artifact_versions v
      ON v.artifact_id = a.artifact_id
    WHERE a.artifact_kind = 'cdg'
),
node_rollup AS (
    SELECT
        n.version_id,
        COUNT(*) AS node_count
    FROM public.artifact_cdg_nodes n
    GROUP BY n.version_id
),
edge_endpoint_rollup AS (
    SELECT
        e.version_id,
        COUNT(*) AS edge_count,
        COUNT(*) FILTER (WHERE source_node.node_id IS NULL) AS missing_edge_source_count,
        COUNT(*) FILTER (WHERE target_node.node_id IS NULL) AS missing_edge_target_count,
        COUNT(*) FILTER (
            WHERE source_node.node_id IS NULL
               OR target_node.node_id IS NULL
        ) AS missing_edge_endpoint_count
    FROM public.artifact_cdg_edges e
    LEFT JOIN public.artifact_cdg_nodes source_node
      ON source_node.version_id = e.version_id
     AND source_node.node_id = e.source_id
    LEFT JOIN public.artifact_cdg_nodes target_node
      ON target_node.version_id = e.version_id
     AND target_node.node_id = e.target_id
    GROUP BY e.version_id
),
binding_node_rollup AS (
    SELECT
        b.version_id,
        COUNT(*) AS binding_count,
        COUNT(*) FILTER (WHERE bound_node.node_id IS NULL) AS missing_binding_node_count
    FROM public.artifact_cdg_bindings b
    LEFT JOIN public.artifact_cdg_nodes bound_node
      ON bound_node.version_id = b.version_id
     AND bound_node.node_id = b.node_id
    GROUP BY b.version_id
),
node_duplicate_key_rollup AS (
    SELECT
        version_id,
        COALESCE(SUM(row_count - 1) FILTER (WHERE row_count > 1), 0)::BIGINT
            AS duplicate_node_key_count
    FROM (
        SELECT
            n.version_id,
            n.node_id,
            COUNT(*) AS row_count
        FROM public.artifact_cdg_nodes n
        GROUP BY n.version_id, n.node_id
    ) duplicate_nodes
    GROUP BY version_id
),
edge_duplicate_key_rollup AS (
    SELECT
        version_id,
        COALESCE(SUM(row_count - 1) FILTER (WHERE row_count > 1), 0)::BIGINT
            AS duplicate_edge_key_count
    FROM (
        SELECT
            e.version_id,
            e.source_id,
            e.target_id,
            e.output_name,
            e.input_name,
            COUNT(*) AS row_count
        FROM public.artifact_cdg_edges e
        GROUP BY e.version_id, e.source_id, e.target_id, e.output_name, e.input_name
    ) duplicate_edges
    GROUP BY version_id
),
binding_duplicate_key_rollup AS (
    SELECT
        version_id,
        COALESCE(SUM(row_count - 1) FILTER (WHERE row_count > 1), 0)::BIGINT
            AS duplicate_binding_key_count
    FROM (
        SELECT
            b.version_id,
            b.node_id,
            b.bound_artifact_fqdn,
            COUNT(*) AS row_count
        FROM public.artifact_cdg_bindings b
        GROUP BY b.version_id, b.node_id, b.bound_artifact_fqdn
    ) duplicate_bindings
    GROUP BY version_id
),
readiness_rows AS (
    SELECT
        vs.artifact_id,
        vs.fqdn,
        vs.artifact_status,
        vs.version_id,
        vs.content_hash,
        vs.semver,
        vs.is_latest,
        COALESCE(nr.node_count, 0) AS node_count,
        COALESCE(eer.edge_count, 0) AS edge_count,
        COALESCE(bnr.binding_count, 0) AS binding_count,
        COALESCE(eer.missing_edge_source_count, 0) AS missing_edge_source_count,
        COALESCE(eer.missing_edge_target_count, 0) AS missing_edge_target_count,
        COALESCE(eer.missing_edge_endpoint_count, 0) AS missing_edge_endpoint_count,
        COALESCE(bnr.missing_binding_node_count, 0) AS missing_binding_node_count,
        COALESCE(ndkr.duplicate_node_key_count, 0) AS duplicate_node_key_count,
        COALESCE(edkr.duplicate_edge_key_count, 0) AS duplicate_edge_key_count,
        COALESCE(bdkr.duplicate_binding_key_count, 0) AS duplicate_binding_key_count,
        (
            COALESCE(ndkr.duplicate_node_key_count, 0)
          + COALESCE(edkr.duplicate_edge_key_count, 0)
          + COALESCE(bdkr.duplicate_binding_key_count, 0)
        ) AS duplicate_key_count,
        ARRAY_REMOVE(ARRAY[
            CASE WHEN vs.artifact_status <> 'approved' THEN 'artifact_not_approved' END,
            CASE WHEN vs.content_hash = '' THEN 'missing_version_content_hash' END,
            CASE WHEN vs.semver = '' THEN 'missing_version_semver' END,
            CASE WHEN COALESCE(nr.node_count, 0) = 0 THEN 'missing_cdg_nodes' END,
            CASE WHEN COALESCE(eer.edge_count, 0) = 0 THEN 'missing_cdg_edges' END,
            CASE WHEN COALESCE(bnr.binding_count, 0) = 0 THEN 'missing_cdg_bindings' END,
            CASE WHEN COALESCE(eer.missing_edge_source_count, 0) > 0 THEN 'missing_edge_source_nodes' END,
            CASE WHEN COALESCE(eer.missing_edge_target_count, 0) > 0 THEN 'missing_edge_target_nodes' END,
            CASE WHEN COALESCE(bnr.missing_binding_node_count, 0) > 0 THEN 'missing_binding_nodes' END,
            CASE WHEN COALESCE(ndkr.duplicate_node_key_count, 0) > 0 THEN 'duplicate_node_keys' END,
            CASE WHEN COALESCE(edkr.duplicate_edge_key_count, 0) > 0 THEN 'duplicate_edge_keys' END,
            CASE WHEN COALESCE(bdkr.duplicate_binding_key_count, 0) > 0 THEN 'duplicate_binding_keys' END
        ], NULL) AS blockers
    FROM version_scope vs
    LEFT JOIN node_rollup nr
      ON nr.version_id = vs.version_id
    LEFT JOIN edge_endpoint_rollup eer
      ON eer.version_id = vs.version_id
    LEFT JOIN binding_node_rollup bnr
      ON bnr.version_id = vs.version_id
    LEFT JOIN node_duplicate_key_rollup ndkr
      ON ndkr.version_id = vs.version_id
    LEFT JOIN edge_duplicate_key_rollup edkr
      ON edkr.version_id = vs.version_id
    LEFT JOIN binding_duplicate_key_rollup bdkr
      ON bdkr.version_id = vs.version_id
)
SELECT
    artifact_id,
    fqdn,
    artifact_status,
    version_id,
    content_hash,
    semver,
    is_latest,
    node_count,
    edge_count,
    binding_count,
    missing_edge_source_count,
    missing_edge_target_count,
    missing_edge_endpoint_count,
    missing_binding_node_count,
    duplicate_node_key_count,
    duplicate_edge_key_count,
    duplicate_binding_key_count,
    duplicate_key_count,
    blockers,
    CARDINALITY(blockers) AS blocker_count,
    CARDINALITY(blockers) = 0 AS graph_ready,
    CASE
        WHEN CARDINALITY(blockers) = 0 THEN 'ready'
        ELSE 'blocked'
    END AS readiness_status
FROM readiness_rows;

CREATE OR REPLACE FUNCTION public.physics_cdg_graph_readiness(
    request_fqdn TEXT DEFAULT NULL,
    request_blocker TEXT DEFAULT NULL,
    request_latest_only BOOLEAN DEFAULT TRUE,
    request_ready_only BOOLEAN DEFAULT FALSE
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'cdg_graph_readiness', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.graph_ready DESC,
                    row_data.blocker_count,
                    row_data.fqdn,
                    row_data.is_latest DESC,
                    row_data.semver,
                    row_data.version_id
            ),
            '[]'::jsonb
        )
        FROM public.physics_cdg_graph_readiness row_data
        WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
          AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
          AND (NOT request_latest_only OR row_data.is_latest)
          AND (NOT request_ready_only OR row_data.graph_ready)
    ),
    'summary', jsonb_build_object(
        'cdg_version_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'graph_ready_version_count', (
            SELECT COUNT(*)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE row_data.graph_ready
              AND (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'node_count', (
            SELECT COALESCE(SUM(node_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'edge_count', (
            SELECT COALESCE(SUM(edge_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'binding_count', (
            SELECT COALESCE(SUM(binding_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'missing_edge_endpoint_count', (
            SELECT COALESCE(SUM(missing_edge_endpoint_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'missing_binding_node_count', (
            SELECT COALESCE(SUM(missing_binding_node_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'duplicate_key_count', (
            SELECT COALESCE(SUM(duplicate_key_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        ),
        'blocker_count', (
            SELECT COALESCE(SUM(blocker_count), 0)
            FROM public.physics_cdg_graph_readiness row_data
            WHERE (request_fqdn IS NULL OR row_data.fqdn = request_fqdn)
              AND (request_blocker IS NULL OR request_blocker = ANY(row_data.blockers))
              AND (NOT request_latest_only OR row_data.is_latest)
              AND (NOT request_ready_only OR row_data.graph_ready)
        )
    )
);
$$;

GRANT SELECT ON public.physics_cdg_graph_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_cdg_graph_readiness(TEXT, TEXT, BOOLEAN, BOOLEAN) TO authenticated;
