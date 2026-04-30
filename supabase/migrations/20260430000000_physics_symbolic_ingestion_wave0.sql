-- Physics symbolic ingestion wave 0.
-- Additive schema contracts for raw physics equation capture, symbolic
-- expression publication, dimensional IO parity, validity bounds, and typed
-- relationships. Existing atom, CDG, and state artifact behavior is unchanged.

ALTER TABLE public.artifact_io_specs
    ADD COLUMN IF NOT EXISTS dim_signature TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_artifact_io_specs_dim_signature
    ON public.artifact_io_specs (dim_signature)
    WHERE dim_signature <> '';

CREATE TABLE IF NOT EXISTS public.physics_ingest_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system TEXT NOT NULL
        CHECK (source_system IN (
            'wikidata',
            'qudt',
            'physics_derivation_graph',
            'nist_codata',
            'nist_dlmf',
            'hitran',
            'materials_project',
            'opb',
            'theoria',
            'phy_srbench',
            'manual'
        )),
    source_version TEXT NOT NULL DEFAULT '',
    source_uri TEXT NOT NULL DEFAULT '',
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    adapter_name TEXT NOT NULL DEFAULT '',
    adapter_version TEXT NOT NULL DEFAULT '',
    license_expression TEXT NOT NULL DEFAULT '',
    provenance_summary TEXT NOT NULL DEFAULT '',
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_version, source_uri, payload_sha256)
);

CREATE INDEX IF NOT EXISTS idx_physics_ingest_snapshots_source
    ON public.physics_ingest_snapshots (source_system, source_version);
CREATE INDEX IF NOT EXISTS idx_physics_ingest_snapshots_payload
    ON public.physics_ingest_snapshots (payload_sha256);

CREATE TABLE IF NOT EXISTS public.physics_equation_candidates (
    candidate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES public.physics_ingest_snapshots(snapshot_id)
        ON DELETE CASCADE,
    source_candidate_id TEXT NOT NULL DEFAULT '',
    source_entity_uri TEXT NOT NULL DEFAULT '',
    source_label TEXT NOT NULL DEFAULT '',
    source_description TEXT NOT NULL DEFAULT '',
    raw_formula TEXT NOT NULL DEFAULT '',
    raw_formula_format TEXT NOT NULL DEFAULT ''
        CHECK (raw_formula_format IN (
            '',
            'latex',
            'mathml',
            'content_mathml',
            'wikidata_math',
            'asciimath',
            'sympy',
            'plain_text'
        )),
    candidate_status TEXT NOT NULL DEFAULT 'raw_imported'
        CHECK (candidate_status IN (
            'raw_imported',
            'parse_failed',
            'parsed',
            'dimension_resolved',
            'symbolically_validated',
            'source_verified',
            'human_reviewed',
            'published',
            'blocked'
        )),
    parse_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (parse_confidence >= 0.0 AND parse_confidence <= 1.0),
    priority_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    mechanism_tags TEXT[] NOT NULL DEFAULT '{}',
    behavioral_archetypes TEXT[] NOT NULL DEFAULT '{}',
    source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, source_candidate_id, raw_formula)
);

CREATE INDEX IF NOT EXISTS idx_physics_equation_candidates_snapshot
    ON public.physics_equation_candidates (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_physics_equation_candidates_status
    ON public.physics_equation_candidates (candidate_status);
CREATE INDEX IF NOT EXISTS idx_physics_equation_candidates_mechanisms
    ON public.physics_equation_candidates USING gin (mechanism_tags);

CREATE TABLE IF NOT EXISTS public.artifact_symbolic_expressions (
    expression_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    candidate_id UUID REFERENCES public.physics_equation_candidates(candidate_id)
        ON DELETE SET NULL,
    expression_kind TEXT NOT NULL
        CHECK (expression_kind IN (
            'equation',
            'identity',
            'inequality',
            'ode',
            'pde',
            'constraint',
            'definition'
        )),
    expression_role TEXT NOT NULL DEFAULT 'primary'
        CHECK (expression_role IN (
            'primary',
            'auxiliary',
            'constraint',
            'assumption',
            'compiled_output'
        )),
    sympy_srepr TEXT NOT NULL DEFAULT '',
    canonical_expr_hash TEXT NOT NULL DEFAULT ''
        CHECK (canonical_expr_hash = '' OR canonical_expr_hash ~ '^[0-9a-f]{64}$'),
    topology_hash TEXT NOT NULL DEFAULT ''
        CHECK (topology_hash = '' OR topology_hash ~ '^[0-9a-f]{64}$'),
    dimensional_hash TEXT NOT NULL DEFAULT ''
        CHECK (dimensional_hash = '' OR dimensional_hash ~ '^[0-9a-f]{64}$'),
    raw_formula TEXT NOT NULL DEFAULT '',
    raw_formula_format TEXT NOT NULL DEFAULT ''
        CHECK (raw_formula_format IN (
            '',
            'latex',
            'mathml',
            'content_mathml',
            'wikidata_math',
            'asciimath',
            'sympy',
            'plain_text'
        )),
    source_expression_id TEXT NOT NULL DEFAULT '',
    parse_status TEXT NOT NULL DEFAULT 'raw_imported'
        CHECK (parse_status IN (
            'raw_imported',
            'parse_failed',
            'parsed',
            'normalized',
            'blocked'
        )),
    parse_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (parse_confidence >= 0.0 AND parse_confidence <= 1.0),
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN (
            'unreviewed',
            'automated_pass',
            'needs_human',
            'human_reviewed',
            'blocked'
        )),
    validation_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (validation_status IN (
            'unknown',
            'passed',
            'failed',
            'skipped'
        )),
    mechanism_tags TEXT[] NOT NULL DEFAULT '{}',
    behavioral_archetypes TEXT[] NOT NULL DEFAULT '{}',
    assumptions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (sympy_srepr <> '' OR raw_formula <> '')
);

CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_artifact
    ON public.artifact_symbolic_expressions (artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_version
    ON public.artifact_symbolic_expressions (version_id);
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_candidate
    ON public.artifact_symbolic_expressions (candidate_id);
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_topology
    ON public.artifact_symbolic_expressions (topology_hash)
    WHERE topology_hash <> '';
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_mechanisms
    ON public.artifact_symbolic_expressions USING gin (mechanism_tags);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_symbolic_expressions_source_id
    ON public.artifact_symbolic_expressions (
        version_id,
        expression_role,
        source_expression_id
    )
    WHERE source_expression_id <> '';

CREATE TABLE IF NOT EXISTS public.artifact_symbolic_variables (
    variable_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expression_id UUID NOT NULL REFERENCES public.artifact_symbolic_expressions(expression_id)
        ON DELETE CASCADE,
    symbol_name TEXT NOT NULL,
    source_symbol TEXT NOT NULL DEFAULT '',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    variable_role TEXT NOT NULL
        CHECK (variable_role IN (
            'input',
            'output',
            'parameter',
            'constant',
            'state',
            'intermediate'
        )),
    quantity_kind_uri TEXT NOT NULL DEFAULT '',
    quantity_kind_label TEXT NOT NULL DEFAULT '',
    unit_uri TEXT NOT NULL DEFAULT '',
    unit_label TEXT NOT NULL DEFAULT '',
    dim_signature TEXT NOT NULL DEFAULT '',
    dimension_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (dimension_source IN (
            'unknown',
            'qudt',
            'source',
            'manual',
            'inferred'
        )),
    assumptions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ordinal INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (expression_id, symbol_name)
);

CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_variables_expression
    ON public.artifact_symbolic_variables (expression_id);
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_variables_dimension
    ON public.artifact_symbolic_variables (dim_signature)
    WHERE dim_signature <> '';
CREATE INDEX IF NOT EXISTS idx_artifact_symbolic_variables_quantity
    ON public.artifact_symbolic_variables (quantity_kind_uri)
    WHERE quantity_kind_uri <> '';

CREATE TABLE IF NOT EXISTS public.artifact_validity_bounds (
    bound_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    expression_id UUID REFERENCES public.artifact_symbolic_expressions(expression_id)
        ON DELETE CASCADE,
    variable_id UUID REFERENCES public.artifact_symbolic_variables(variable_id)
        ON DELETE SET NULL,
    scope TEXT NOT NULL DEFAULT 'expression'
        CHECK (scope IN ('artifact', 'version', 'expression', 'variable', 'edge')),
    bound_kind TEXT NOT NULL DEFAULT 'domain'
        CHECK (bound_kind IN (
            'domain',
            'regime',
            'approximation',
            'replacement',
            'assumption'
        )),
    variable_name TEXT NOT NULL DEFAULT '',
    lower_value DOUBLE PRECISION,
    upper_value DOUBLE PRECISION,
    lower_inclusive BOOLEAN NOT NULL DEFAULT TRUE,
    upper_inclusive BOOLEAN NOT NULL DEFAULT TRUE,
    unit_uri TEXT NOT NULL DEFAULT '',
    dim_signature TEXT NOT NULL DEFAULT '',
    regime_label TEXT NOT NULL DEFAULT '',
    validity_statement TEXT NOT NULL DEFAULT '',
    replacement_artifact_fqdn TEXT NOT NULL DEFAULT '',
    evidence_ref_key TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT ''
        CHECK (confidence IN ('', 'low', 'medium', 'high')),
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN (
            'unreviewed',
            'automated_pass',
            'needs_human',
            'human_reviewed',
            'blocked'
        )),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        lower_value IS NULL
        OR upper_value IS NULL
        OR lower_value <= upper_value
    )
);

CREATE INDEX IF NOT EXISTS idx_artifact_validity_bounds_artifact
    ON public.artifact_validity_bounds (artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_validity_bounds_version
    ON public.artifact_validity_bounds (version_id);
CREATE INDEX IF NOT EXISTS idx_artifact_validity_bounds_expression
    ON public.artifact_validity_bounds (expression_id);
CREATE INDEX IF NOT EXISTS idx_artifact_validity_bounds_regime
    ON public.artifact_validity_bounds (regime_label)
    WHERE regime_label <> '';

CREATE TABLE IF NOT EXISTS public.artifact_relationships (
    relationship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_artifact_id UUID REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    source_version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    source_expression_id UUID REFERENCES public.artifact_symbolic_expressions(expression_id)
        ON DELETE CASCADE,
    target_artifact_id UUID REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    target_version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    target_expression_id UUID REFERENCES public.artifact_symbolic_expressions(expression_id)
        ON DELETE CASCADE,
    relationship_kind TEXT NOT NULL
        CHECK (relationship_kind IN (
            'same_math_topology_as',
            'physical_grounding_of',
            'derives_from',
            'limit_case_of',
            'approximation_of',
            'uses_constant',
            'uses_data_artifact',
            'has_use',
            'mechanism_analogue_of',
            'algebraic_rearrangement_of',
            'requires_assumption',
            'replaces_outside_regime'
        )),
    relationship_label TEXT NOT NULL DEFAULT '',
    source_node_id TEXT NOT NULL DEFAULT '',
    target_node_id TEXT NOT NULL DEFAULT '',
    inference_rule_id TEXT NOT NULL DEFAULT '',
    binding_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    assumptions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source_kind TEXT NOT NULL DEFAULT 'manual'
        CHECK (source_kind IN (
            'manual',
            'wikidata',
            'physics_derivation_graph',
            'qudt',
            'nist',
            'llm_assisted',
            'automated'
        )),
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        source_artifact_id IS NOT NULL
        OR source_version_id IS NOT NULL
        OR source_expression_id IS NOT NULL
    ),
    CHECK (
        target_artifact_id IS NOT NULL
        OR target_version_id IS NOT NULL
        OR target_expression_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_artifact_relationships_source_artifact
    ON public.artifact_relationships (source_artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_relationships_target_artifact
    ON public.artifact_relationships (target_artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_relationships_source_expression
    ON public.artifact_relationships (source_expression_id);
CREATE INDEX IF NOT EXISTS idx_artifact_relationships_target_expression
    ON public.artifact_relationships (target_expression_id);
CREATE INDEX IF NOT EXISTS idx_artifact_relationships_kind
    ON public.artifact_relationships (relationship_kind);
CREATE INDEX IF NOT EXISTS idx_artifact_relationships_verified
    ON public.artifact_relationships (verified)
    WHERE verified = TRUE;

DROP VIEW IF EXISTS public.catalog_symbolic_artifacts CASCADE;
CREATE VIEW public.catalog_symbolic_artifacts AS
SELECT
    a.artifact_id,
    a.artifact_kind,
    a.fqdn,
    a.status,
    a.visibility_tier,
    v.version_id,
    v.semver,
    se.expression_id,
    se.expression_kind,
    se.expression_role,
    se.canonical_expr_hash,
    se.topology_hash,
    se.dimensional_hash,
    se.parse_status,
    se.review_status,
    se.validation_status,
    se.mechanism_tags,
    se.behavioral_archetypes,
    COALESCE(var_counts.variable_count, 0) AS variable_count,
    COALESCE(bound_counts.validity_bound_count, 0) AS validity_bound_count,
    COALESCE(rel_counts.relationship_count, 0) AS relationship_count
FROM public.artifact_symbolic_expressions se
JOIN public.artifacts a
  ON a.artifact_id = se.artifact_id
JOIN public.artifact_versions v
  ON v.version_id = se.version_id
LEFT JOIN (
    SELECT expression_id, COUNT(*) AS variable_count
    FROM public.artifact_symbolic_variables
    GROUP BY expression_id
) AS var_counts
  ON var_counts.expression_id = se.expression_id
LEFT JOIN (
    SELECT expression_id, COUNT(*) AS validity_bound_count
    FROM public.artifact_validity_bounds
    WHERE expression_id IS NOT NULL
    GROUP BY expression_id
) AS bound_counts
  ON bound_counts.expression_id = se.expression_id
LEFT JOIN (
    SELECT expression_id, SUM(relationship_count) AS relationship_count
    FROM (
        SELECT source_expression_id AS expression_id, COUNT(*) AS relationship_count
        FROM public.artifact_relationships
        WHERE source_expression_id IS NOT NULL
        GROUP BY source_expression_id
        UNION ALL
        SELECT target_expression_id AS expression_id, COUNT(*) AS relationship_count
        FROM public.artifact_relationships
        WHERE target_expression_id IS NOT NULL
        GROUP BY target_expression_id
    ) AS expression_relationship_counts
    GROUP BY expression_id
) AS rel_counts
  ON rel_counts.expression_id = se.expression_id;

GRANT SELECT ON public.catalog_symbolic_artifacts TO authenticated;

CREATE OR REPLACE FUNCTION public.get_artifact_document(request_fqdn TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
WITH matched_artifact AS (
    SELECT a.*
    FROM public.artifacts a
    WHERE a.fqdn = request_fqdn
    LIMIT 1
)
SELECT CASE
    WHEN EXISTS (SELECT 1 FROM matched_artifact) THEN (
        SELECT jsonb_build_object(
            'artifact', row_to_json(a),
            'source_repository', (
                SELECT row_to_json(sr)
                FROM public.atom_source_repositories sr
                WHERE sr.source_repo_id = a.source_repo_id
            ),
            'descriptions', (
                SELECT jsonb_agg(row_to_json(d) ORDER BY d.kind, d.language)
                FROM public.artifact_descriptions d
                WHERE d.artifact_id = a.artifact_id
            ),
            'io_specs', (
                SELECT jsonb_agg(row_to_json(ios) ORDER BY ios.direction, ios.ordinal)
                FROM public.artifact_io_specs ios
                WHERE ios.artifact_id = a.artifact_id
            ),
            'parameters', (
                SELECT jsonb_agg(row_to_json(p) ORDER BY p.position)
                FROM public.artifact_parameters p
                WHERE p.artifact_id = a.artifact_id
            ),
            'references', (
                SELECT jsonb_agg(row_to_json(r) ORDER BY r.year NULLS LAST, r.title)
                FROM public.artifact_references r
                WHERE r.artifact_id = a.artifact_id
            ),
            'audit_rollup', (
                SELECT row_to_json(ar)
                FROM public.artifact_audit_rollups ar
                WHERE ar.artifact_id = a.artifact_id
            ),
            'audit_latest', (
                SELECT jsonb_agg(row_to_json(latest_row) ORDER BY latest_row.audit_type)
                FROM (
                    SELECT DISTINCT ON (e.audit_type)
                        e.artifact_id,
                        e.audit_type,
                        e.passed,
                        e.status,
                        e.details,
                        e.source_kind,
                        e.runner_version,
                        e.source_revision,
                        e.upstream_version,
                        e.created_at AS audited_at
                    FROM public.artifact_audit_evidence e
                    WHERE e.artifact_id = a.artifact_id
                    ORDER BY e.audit_type, e.created_at DESC
                ) AS latest_row
            ),
            'uncertainty_estimates', (
                SELECT jsonb_agg(row_to_json(ue) ORDER BY ue.created_at DESC)
                FROM public.artifact_uncertainty_estimates ue
                WHERE ue.artifact_id = a.artifact_id
            ),
            'verification_matches', (
                SELECT jsonb_agg(
                    row_to_json(vm)
                    ORDER BY vm.verification_level, vm.candidate_score DESC NULLS LAST
                )
                FROM public.artifact_verification_matches vm
                WHERE vm.artifact_id = a.artifact_id
            ),
            'symbolic_expressions', (
                SELECT jsonb_agg(row_to_json(se) ORDER BY se.expression_role, se.expression_kind)
                FROM public.artifact_symbolic_expressions se
                WHERE se.artifact_id = a.artifact_id
            ),
            'symbolic_variables', (
                SELECT jsonb_agg(row_to_json(sv) ORDER BY se.expression_role, sv.ordinal, sv.symbol_name)
                FROM public.artifact_symbolic_variables sv
                JOIN public.artifact_symbolic_expressions se
                  ON se.expression_id = sv.expression_id
                WHERE se.artifact_id = a.artifact_id
            ),
            'validity_bounds', (
                SELECT jsonb_agg(row_to_json(vb) ORDER BY vb.scope, vb.bound_kind, vb.variable_name)
                FROM public.artifact_validity_bounds vb
                WHERE vb.artifact_id = a.artifact_id
            ),
            'relationships', (
                SELECT jsonb_agg(row_to_json(rel) ORDER BY rel.relationship_kind, rel.created_at)
                FROM public.artifact_relationships rel
                WHERE rel.source_artifact_id = a.artifact_id
                   OR rel.target_artifact_id = a.artifact_id
                   OR rel.source_version_id IN (
                        SELECT v.version_id
                        FROM public.artifact_versions v
                        WHERE v.artifact_id = a.artifact_id
                   )
                   OR rel.target_version_id IN (
                        SELECT v.version_id
                        FROM public.artifact_versions v
                        WHERE v.artifact_id = a.artifact_id
                   )
                   OR rel.source_expression_id IN (
                        SELECT se.expression_id
                        FROM public.artifact_symbolic_expressions se
                        WHERE se.artifact_id = a.artifact_id
                   )
                   OR rel.target_expression_id IN (
                        SELECT se.expression_id
                        FROM public.artifact_symbolic_expressions se
                        WHERE se.artifact_id = a.artifact_id
                   )
            ),
            'cdg_nodes', (
                SELECT jsonb_agg(row_to_json(n) ORDER BY n.node_id)
                FROM public.artifact_cdg_nodes n
                JOIN public.artifact_versions v ON v.version_id = n.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'cdg_edges', (
                SELECT jsonb_agg(row_to_json(e) ORDER BY e.source_id, e.target_id)
                FROM public.artifact_cdg_edges e
                JOIN public.artifact_versions v ON v.version_id = e.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'cdg_bindings', (
                SELECT jsonb_agg(row_to_json(b) ORDER BY b.node_id, b.bound_artifact_fqdn)
                FROM public.artifact_cdg_bindings b
                JOIN public.artifact_versions v ON v.version_id = b.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'assets', (
                SELECT jsonb_agg(row_to_json(aa) ORDER BY aa.asset_path)
                FROM public.artifact_assets aa
                JOIN public.artifact_versions v ON v.version_id = aa.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'state_metadata', (
                SELECT jsonb_agg(row_to_json(sam) ORDER BY v.created_at DESC)
                FROM public.state_artifact_metadata sam
                JOIN public.artifact_versions v ON v.version_id = sam.version_id
                WHERE v.artifact_id = a.artifact_id
            ),
            'state_ports', (
                SELECT jsonb_agg(row_to_json(sp) ORDER BY sp.ordinal, sp.port_name)
                FROM public.artifact_state_ports sp
                WHERE sp.artifact_id = a.artifact_id
            ),
            'dependencies', (
                SELECT jsonb_agg(
                    row_to_json(dep)
                    ORDER BY dep.dependency_role, dep.dependency_artifact_fqdn, dep.port_name
                )
                FROM public.artifact_dependencies dep
                JOIN public.artifact_versions v ON v.version_id = dep.dependent_version_id
                WHERE v.artifact_id = a.artifact_id
            )
        )
        FROM matched_artifact a
    )
    ELSE public.get_atom_document(request_fqdn)
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_artifact_document(TEXT) TO authenticated;

CREATE OR REPLACE FUNCTION public.symbolic_artifact_coverage()
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
SELECT jsonb_build_object(
    'candidate_count', (
        SELECT COUNT(*) FROM public.physics_equation_candidates
    ),
    'candidate_status_counts', (
        SELECT COALESCE(jsonb_object_agg(candidate_status, status_count), '{}'::jsonb)
        FROM (
            SELECT candidate_status, COUNT(*) AS status_count
            FROM public.physics_equation_candidates
            GROUP BY candidate_status
        ) AS counts
    ),
    'symbolic_artifact_count', (
        SELECT COUNT(DISTINCT artifact_id)
        FROM public.artifact_symbolic_expressions
    ),
    'symbolic_expression_count', (
        SELECT COUNT(*) FROM public.artifact_symbolic_expressions
    ),
    'review_status_counts', (
        SELECT COALESCE(jsonb_object_agg(review_status, status_count), '{}'::jsonb)
        FROM (
            SELECT review_status, COUNT(*) AS status_count
            FROM public.artifact_symbolic_expressions
            GROUP BY review_status
        ) AS counts
    )
);
$$;

GRANT EXECUTE ON FUNCTION public.symbolic_artifact_coverage() TO authenticated;

ALTER TABLE public.physics_ingest_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.physics_equation_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.artifact_symbolic_expressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.artifact_symbolic_variables ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.artifact_validity_bounds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.artifact_relationships ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS physics_ingest_snapshots_select
    ON public.physics_ingest_snapshots;
DROP POLICY IF EXISTS physics_equation_candidates_select
    ON public.physics_equation_candidates;
DROP POLICY IF EXISTS artifact_symbolic_expressions_select
    ON public.artifact_symbolic_expressions;
DROP POLICY IF EXISTS artifact_symbolic_variables_select
    ON public.artifact_symbolic_variables;
DROP POLICY IF EXISTS artifact_validity_bounds_select
    ON public.artifact_validity_bounds;
DROP POLICY IF EXISTS artifact_relationships_select
    ON public.artifact_relationships;

CREATE POLICY physics_ingest_snapshots_select
    ON public.physics_ingest_snapshots FOR SELECT
    USING (TRUE);
CREATE POLICY physics_equation_candidates_select
    ON public.physics_equation_candidates FOR SELECT
    USING (TRUE);
CREATE POLICY artifact_symbolic_expressions_select
    ON public.artifact_symbolic_expressions FOR SELECT
    USING (TRUE);
CREATE POLICY artifact_symbolic_variables_select
    ON public.artifact_symbolic_variables FOR SELECT
    USING (TRUE);
CREATE POLICY artifact_validity_bounds_select
    ON public.artifact_validity_bounds FOR SELECT
    USING (TRUE);
CREATE POLICY artifact_relationships_select
    ON public.artifact_relationships FOR SELECT
    USING (TRUE);

DROP POLICY IF EXISTS physics_ingest_snapshots_service_role_all
    ON public.physics_ingest_snapshots;
DROP POLICY IF EXISTS physics_equation_candidates_service_role_all
    ON public.physics_equation_candidates;
DROP POLICY IF EXISTS artifact_symbolic_expressions_service_role_all
    ON public.artifact_symbolic_expressions;
DROP POLICY IF EXISTS artifact_symbolic_variables_service_role_all
    ON public.artifact_symbolic_variables;
DROP POLICY IF EXISTS artifact_validity_bounds_service_role_all
    ON public.artifact_validity_bounds;
DROP POLICY IF EXISTS artifact_relationships_service_role_all
    ON public.artifact_relationships;

CREATE POLICY physics_ingest_snapshots_service_role_all
    ON public.physics_ingest_snapshots FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
CREATE POLICY physics_equation_candidates_service_role_all
    ON public.physics_equation_candidates FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
CREATE POLICY artifact_symbolic_expressions_service_role_all
    ON public.artifact_symbolic_expressions FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
CREATE POLICY artifact_symbolic_variables_service_role_all
    ON public.artifact_symbolic_variables FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
CREATE POLICY artifact_validity_bounds_service_role_all
    ON public.artifact_validity_bounds FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
CREATE POLICY artifact_relationships_service_role_all
    ON public.artifact_relationships FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
