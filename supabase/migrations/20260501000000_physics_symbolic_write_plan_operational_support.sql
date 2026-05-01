-- Physics symbolic write-plan operational support.
-- Additive conflict targets and source-ID lookup surfaces for deterministic
-- loader execution. Table shapes, triggers, policies, and existing write paths
-- are unchanged.

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_relationships_pdg_source_edge
    ON public.artifact_relationships (
        source_kind,
        relationship_kind,
        source_expression_id,
        target_expression_id,
        source_node_id,
        target_node_id,
        inference_rule_id
    )
    WHERE source_kind = 'physics_derivation_graph'
      AND source_expression_id IS NOT NULL
      AND target_expression_id IS NOT NULL
      AND source_node_id <> ''
      AND target_node_id <> ''
      AND inference_rule_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_validity_bounds_expression_evidence
    ON public.artifact_validity_bounds (
        expression_id,
        scope,
        bound_kind,
        variable_name,
        evidence_ref_key
    )
    WHERE expression_id IS NOT NULL
      AND evidence_ref_key <> '';

CREATE OR REPLACE VIEW public.physics_symbolic_write_plan_source_ids AS
SELECT
    s.source_system,
    s.source_version,
    s.source_uri,
    s.snapshot_id,
    s.payload_sha256,
    c.candidate_id,
    c.source_candidate_id,
    c.raw_formula AS candidate_raw_formula,
    c.candidate_status,
    se.expression_id,
    se.source_expression_id,
    se.expression_role,
    se.expression_kind,
    se.parse_status,
    se.review_status,
    se.validation_status,
    se.artifact_id,
    a.fqdn,
    se.version_id,
    v.semver,
    se.canonical_expr_hash,
    se.topology_hash,
    se.dimensional_hash
FROM public.physics_ingest_snapshots s
LEFT JOIN public.physics_equation_candidates c
  ON c.snapshot_id = s.snapshot_id
LEFT JOIN public.artifact_symbolic_expressions se
  ON se.candidate_id = c.candidate_id
LEFT JOIN public.artifacts a
  ON a.artifact_id = se.artifact_id
LEFT JOIN public.artifact_versions v
  ON v.version_id = se.version_id;

CREATE OR REPLACE FUNCTION public.physics_symbolic_write_plan_source_id_map(
    request_source_system TEXT DEFAULT NULL,
    request_source_version TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT COALESCE(
    jsonb_agg(
        to_jsonb(row_data)
        ORDER BY
            row_data.source_system,
            row_data.source_version,
            row_data.source_uri,
            row_data.source_candidate_id,
            row_data.source_expression_id,
            row_data.fqdn,
            row_data.semver
    ),
    '[]'::jsonb
)
FROM public.physics_symbolic_write_plan_source_ids row_data
WHERE (request_source_system IS NULL OR row_data.source_system = request_source_system)
  AND (request_source_version IS NULL OR row_data.source_version = request_source_version);
$$;

GRANT SELECT ON public.physics_symbolic_write_plan_source_ids TO authenticated;
GRANT EXECUTE ON FUNCTION public.physics_symbolic_write_plan_source_id_map(TEXT, TEXT) TO authenticated;
