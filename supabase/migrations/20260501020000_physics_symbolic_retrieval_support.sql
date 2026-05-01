-- Physics symbolic retrieval support.
-- Additive read-only retrieval surfaces for synthesis agents that need
-- expression-level symbolic rows and source-family replay readiness. Existing
-- table shape, triggers, policies, and write paths are unchanged.

CREATE OR REPLACE VIEW public.physics_symbolic_retrieval_rows AS
WITH candidate_scope AS (
    SELECT
        s.source_system,
        s.source_version,
        s.source_uri,
        s.license_expression,
        s.payload_sha256,
        COALESCE(
            NULLIF(c.source_payload->>'source_family', ''),
            NULLIF(c.source_payload->>'family', ''),
            NULLIF(s.payload->>'source_family', ''),
            NULLIF(s.payload->>'family', ''),
            s.source_system
        ) AS source_family,
        c.candidate_id,
        c.source_candidate_id,
        c.source_entity_uri,
        c.source_label,
        c.candidate_status,
        c.priority_score,
        c.mechanism_tags AS candidate_mechanism_tags,
        c.behavioral_archetypes AS candidate_behavioral_archetypes
    FROM public.physics_ingest_snapshots s
    JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
),
source_family_suggestions AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(candidate_id) AS raw_suggestion_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status = 'raw_imported') AS raw_imported_suggestion_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN ('parse_failed', 'blocked')) AS blocked_or_failed_suggestion_count
    FROM candidate_scope
    GROUP BY source_system, source_version, source_family
),
variable_rollup AS (
    SELECT
        sv.expression_id,
        COUNT(*) AS variable_count,
        COUNT(*) FILTER (WHERE sv.variable_role <> 'intermediate') AS retrieval_variable_count,
        COUNT(*) FILTER (
            WHERE sv.variable_role <> 'intermediate'
              AND sv.dim_signature = ''
        ) AS missing_dimension_signature_count,
        COUNT(*) FILTER (
            WHERE sv.variable_role <> 'intermediate'
              AND sv.dimension_source IN ('', 'unknown')
        ) AS unknown_dimension_source_count,
        COALESCE(
            ARRAY_AGG(DISTINCT sv.dim_signature ORDER BY sv.dim_signature)
                FILTER (WHERE sv.dim_signature <> ''),
            '{}'::TEXT[]
        ) AS dimension_signatures,
        COALESCE(
            ARRAY_AGG(DISTINCT sv.quantity_kind_uri ORDER BY sv.quantity_kind_uri)
                FILTER (WHERE sv.quantity_kind_uri <> ''),
            '{}'::TEXT[]
        ) AS quantity_kind_uris,
        COALESCE(
            ARRAY_AGG(DISTINCT sv.dimension_source ORDER BY sv.dimension_source)
                FILTER (WHERE sv.dimension_source <> ''),
            '{}'::TEXT[]
        ) AS dimension_sources
    FROM public.artifact_symbolic_variables sv
    GROUP BY sv.expression_id
),
relationship_rollup AS (
    SELECT
        expression_id,
        COUNT(*) AS relationship_count,
        COUNT(*) FILTER (WHERE verified) AS verified_relationship_count,
        COUNT(*) FILTER (
            WHERE source_kind = 'physics_derivation_graph'
              AND source_expression_id IS NOT NULL
              AND target_expression_id IS NOT NULL
              AND source_node_id <> ''
              AND target_node_id <> ''
              AND inference_rule_id <> ''
        ) AS pdg_replay_ready_relationship_count
    FROM (
        SELECT
            source_expression_id AS expression_id,
            verified,
            source_kind,
            source_expression_id,
            target_expression_id,
            source_node_id,
            target_node_id,
            inference_rule_id
        FROM public.artifact_relationships
        WHERE source_expression_id IS NOT NULL
        UNION ALL
        SELECT
            target_expression_id AS expression_id,
            verified,
            source_kind,
            source_expression_id,
            target_expression_id,
            source_node_id,
            target_node_id,
            inference_rule_id
        FROM public.artifact_relationships
        WHERE target_expression_id IS NOT NULL
    ) relationships
    GROUP BY expression_id
)
SELECT
    se.expression_id,
    se.artifact_id,
    a.fqdn,
    a.artifact_kind,
    a.visibility_tier,
    a.source_repo_id,
    sr.repo_name AS source_repo_name,
    sr.repo_url AS source_repo_url,
    sr.default_branch AS source_repo_default_branch,
    a.source_package,
    a.source_module_path,
    a.source_symbol,
    se.version_id,
    v.semver,
    se.candidate_id,
    cs.source_system,
    cs.source_version,
    cs.source_family,
    cs.source_uri,
    cs.license_expression,
    cs.payload_sha256,
    cs.source_candidate_id,
    cs.source_entity_uri,
    cs.source_label,
    cs.candidate_status,
    se.source_expression_id,
    se.expression_kind,
    se.expression_role,
    se.parse_status,
    se.review_status,
    se.validation_status,
    se.parse_confidence,
    cs.priority_score,
    se.canonical_expr_hash,
    se.topology_hash,
    se.dimensional_hash,
    ARRAY(
        SELECT DISTINCT tag
        FROM unnest(se.mechanism_tags || COALESCE(cs.candidate_mechanism_tags, '{}'::TEXT[])) AS tag
        WHERE tag <> ''
        ORDER BY tag
    ) AS mechanism_tags,
    ARRAY(
        SELECT DISTINCT archetype
        FROM unnest(se.behavioral_archetypes || COALESCE(cs.candidate_behavioral_archetypes, '{}'::TEXT[])) AS archetype
        WHERE archetype <> ''
        ORDER BY archetype
    ) AS behavioral_archetypes,
    COALESCE(vr.variable_count, 0) AS variable_count,
    COALESCE(vr.retrieval_variable_count, 0) AS retrieval_variable_count,
    COALESCE(vr.dimension_signatures, '{}'::TEXT[]) AS dimension_signatures,
    COALESCE(vr.quantity_kind_uris, '{}'::TEXT[]) AS quantity_kind_uris,
    COALESCE(vr.dimension_sources, '{}'::TEXT[]) AS dimension_sources,
    COALESCE(vr.missing_dimension_signature_count, 0) AS missing_dimension_signature_count,
    COALESCE(vr.unknown_dimension_source_count, 0) AS unknown_dimension_source_count,
    COALESCE(rr.relationship_count, 0) AS relationship_count,
    COALESCE(rr.verified_relationship_count, 0) AS verified_relationship_count,
    COALESCE(rr.pdg_replay_ready_relationship_count, 0) AS pdg_replay_ready_relationship_count,
    COALESCE(ar.overall_verdict, 'unknown') AS overall_verdict,
    COALESCE(ar.risk_tier, 'medium') AS risk_tier,
    COALESCE(ar.risk_score, 0) AS risk_score,
    COALESCE(ar.acceptability_score, 0) AS acceptability_score,
    COALESCE(ar.trust_readiness, 'not_ready') AS trust_readiness,
    COALESCE(ar.trust_blockers, '{}'::TEXT[]) AS trust_blockers,
    COALESCE(sfs.raw_suggestion_count, 0) AS raw_suggestion_count,
    COALESCE(sfs.raw_imported_suggestion_count, 0) AS raw_imported_suggestion_count,
    COALESCE(sfs.blocked_or_failed_suggestion_count, 0) AS blocked_or_failed_suggestion_count,
    (
        se.topology_hash <> ''
        AND se.dimensional_hash <> ''
        AND COALESCE(vr.retrieval_variable_count, 0) > 0
        AND COALESCE(vr.missing_dimension_signature_count, 0) = 0
        AND COALESCE(vr.unknown_dimension_source_count, 0) = 0
        AND se.parse_status IN ('parsed', 'normalized')
        AND se.review_status IN ('automated_pass', 'human_reviewed')
        AND se.validation_status = 'passed'
        AND COALESCE(ar.overall_verdict, 'unknown') NOT IN ('broken', 'misleading')
        AND COALESCE(ar.trust_readiness, 'not_ready') IN (
            'reviewed_with_limits',
            'catalog_ready',
            'ready_for_manifest_merge',
            'ready'
        )
    ) AS retrieval_ready,
    (
        se.source_expression_id <> ''
        AND se.canonical_expr_hash <> ''
        AND se.topology_hash <> ''
        AND se.dimensional_hash <> ''
    ) AS replay_ready
FROM public.artifact_symbolic_expressions se
JOIN public.artifacts a
  ON a.artifact_id = se.artifact_id
JOIN public.artifact_versions v
  ON v.version_id = se.version_id
LEFT JOIN candidate_scope cs
  ON cs.candidate_id = se.candidate_id
LEFT JOIN source_family_suggestions sfs
  ON sfs.source_system = cs.source_system
 AND sfs.source_version = cs.source_version
 AND sfs.source_family = cs.source_family
LEFT JOIN variable_rollup vr
  ON vr.expression_id = se.expression_id
LEFT JOIN relationship_rollup rr
  ON rr.expression_id = se.expression_id
LEFT JOIN public.artifact_audit_rollups ar
  ON ar.artifact_id = se.artifact_id
LEFT JOIN public.atom_source_repositories sr
  ON sr.source_repo_id = a.source_repo_id;

CREATE OR REPLACE VIEW public.physics_symbolic_source_retrieval_replay_readiness AS
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
        s.source_uri,
        s.license_expression,
        s.payload_sha256,
        c.candidate_id,
        c.source_candidate_id,
        c.candidate_status
    FROM public.physics_ingest_snapshots s
    LEFT JOIN public.physics_equation_candidates c
      ON c.snapshot_id = s.snapshot_id
),
candidate_rollup AS (
    SELECT
        source_system,
        source_version,
        source_family,
        COUNT(DISTINCT snapshot_id) AS snapshot_count,
        COUNT(DISTINCT source_uri) FILTER (WHERE source_uri <> '') AS source_uri_count,
        COUNT(DISTINCT license_expression) FILTER (WHERE license_expression <> '') AS license_expression_count,
        COUNT(DISTINCT payload_sha256) AS payload_sha256_count,
        COUNT(candidate_id) AS raw_suggestion_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status = 'raw_imported') AS raw_imported_suggestion_count,
        COUNT(candidate_id) FILTER (WHERE candidate_status IN ('parse_failed', 'blocked')) AS blocked_or_failed_suggestion_count,
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
        COUNT(expression_id) FILTER (WHERE retrieval_ready) AS retrieval_ready_expression_count,
        COUNT(expression_id) FILTER (WHERE replay_ready) AS replay_ready_expression_count,
        COUNT(expression_id) FILTER (WHERE source_expression_id <> '') AS source_expression_id_ready_count,
        COUNT(expression_id) FILTER (WHERE topology_hash <> '') AS topology_ready_expression_count,
        COUNT(expression_id) FILTER (WHERE dimensional_hash <> '') AS dimensional_hash_ready_expression_count
    FROM public.physics_symbolic_retrieval_rows
    GROUP BY source_system, source_version, source_family
)
SELECT
    cr.source_system,
    cr.source_version,
    cr.source_family,
    cr.snapshot_count,
    cr.source_uri_count,
    cr.license_expression_count,
    cr.payload_sha256_count,
    cr.raw_suggestion_count,
    cr.raw_imported_suggestion_count,
    cr.blocked_or_failed_suggestion_count,
    cr.source_candidate_id_ready_count,
    COALESCE(er.symbolic_expression_count, 0) AS symbolic_expression_count,
    COALESCE(er.retrieval_ready_expression_count, 0) AS retrieval_ready_expression_count,
    COALESCE(er.replay_ready_expression_count, 0) AS replay_ready_expression_count,
    COALESCE(er.source_expression_id_ready_count, 0) AS source_expression_id_ready_count,
    COALESCE(er.topology_ready_expression_count, 0) AS topology_ready_expression_count,
    COALESCE(er.dimensional_hash_ready_expression_count, 0) AS dimensional_hash_ready_expression_count
FROM candidate_rollup cr
LEFT JOIN expression_rollup er
  ON er.source_system = cr.source_system
 AND er.source_version = cr.source_version
 AND er.source_family = cr.source_family;

CREATE OR REPLACE FUNCTION public.physics_symbolic_retrieval_index(
    request_topology_hash TEXT DEFAULT NULL,
    request_dimensional_hash TEXT DEFAULT NULL,
    request_mechanism_tag TEXT DEFAULT NULL,
    request_source_family TEXT DEFAULT NULL,
    request_trust_readiness TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'symbolic_rows', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY
                    row_data.retrieval_ready DESC,
                    row_data.priority_score DESC,
                    row_data.fqdn,
                    row_data.semver,
                    row_data.expression_role,
                    row_data.expression_id
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_retrieval_rows row_data
        WHERE (request_topology_hash IS NULL OR row_data.topology_hash = request_topology_hash)
          AND (request_dimensional_hash IS NULL OR row_data.dimensional_hash = request_dimensional_hash)
          AND (request_mechanism_tag IS NULL OR request_mechanism_tag = ANY(row_data.mechanism_tags))
          AND (request_source_family IS NULL OR row_data.source_family = request_source_family)
          AND (request_trust_readiness IS NULL OR row_data.trust_readiness = request_trust_readiness)
    ),
    'source_replay_readiness', (
        SELECT COALESCE(
            jsonb_agg(
                to_jsonb(row_data)
                ORDER BY row_data.source_system, row_data.source_version, row_data.source_family
            ),
            '[]'::jsonb
        )
        FROM public.physics_symbolic_source_retrieval_replay_readiness row_data
        WHERE (request_source_family IS NULL OR row_data.source_family = request_source_family)
    )
);
$$;

GRANT SELECT ON public.physics_symbolic_retrieval_rows TO authenticated;
GRANT SELECT ON public.physics_symbolic_source_retrieval_replay_readiness TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_symbolic_retrieval_index(TEXT, TEXT, TEXT, TEXT, TEXT) TO authenticated;
