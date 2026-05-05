-- Heuristic registry and CDG binding evidence tables.
-- Enables domain-scoped heuristic discovery and per-binding provenance trails.

-- ---------------------------------------------------------------------------
-- 1. Enum types (mirror Python enums in sciona-matcher)
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE public.heuristic_evidence_type AS ENUM (
        'scalar_score',
        'boolean_flag',
        'distribution_summary',
        'categorical_label',
        'structured_summary'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE public.heuristic_producer_kind AS ENUM (
        'atom_output',
        'diagnostic_atom',
        'runtime_transform',
        'compatibility_mapping'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE public.heuristic_applicability_scope AS ENUM (
        'cross_family',
        'family_local',
        'skeleton_local'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE public.heuristic_action_class AS ENUM (
        'precondition',
        'replace_stage',
        'split_stage',
        'insert_correction',
        'gate_or_validate',
        'smooth_or_aggregate',
        'branch_and_compare'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE public.cdg_binding_status AS ENUM (
        'active',
        'superseded',
        'rejected'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Heuristic registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.heuristic_registry (
    heuristic_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    dejargonized_meaning TEXT NOT NULL DEFAULT '',
    evidence_type public.heuristic_evidence_type NOT NULL,
    value_kind TEXT NOT NULL DEFAULT '',
    value_shape TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    producer_kind public.heuristic_producer_kind NOT NULL DEFAULT 'atom_output',
    applicability_scope public.heuristic_applicability_scope NOT NULL DEFAULT 'cross_family',
    supported_action_classes public.heuristic_action_class[] NOT NULL DEFAULT '{}',
    provenance_requirements TEXT[] NOT NULL DEFAULT '{}',
    domain TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    source_atom_fqdn TEXT NOT NULL DEFAULT '',
    uncertainty_notes TEXT[] NOT NULL DEFAULT '{}',
    "references" JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_heuristic_registry_domain
    ON public.heuristic_registry (domain);
CREATE INDEX IF NOT EXISTS idx_heuristic_registry_family
    ON public.heuristic_registry (family);
CREATE INDEX IF NOT EXISTS idx_heuristic_registry_producer
    ON public.heuristic_registry (producer_kind);

-- ---------------------------------------------------------------------------
-- 3. CDG binding evidence (per-heuristic breakdown)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.artifact_cdg_binding_evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id UUID NOT NULL
        REFERENCES public.artifact_cdg_bindings(binding_id) ON DELETE CASCADE,
    heuristic_id TEXT NOT NULL
        REFERENCES public.heuristic_registry(heuristic_id),
    metric_name TEXT NOT NULL DEFAULT '',
    metric_value DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    action_class public.heuristic_action_class NOT NULL DEFAULT 'precondition',
    reasoning TEXT NOT NULL DEFAULT '',
    alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
    thresholds_applied JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_binding_evidence_binding
    ON public.artifact_cdg_binding_evidence (binding_id);
CREATE INDEX IF NOT EXISTS idx_binding_evidence_heuristic
    ON public.artifact_cdg_binding_evidence (heuristic_id);

-- ---------------------------------------------------------------------------
-- 4. Extend artifact_cdg_bindings with action class and evidence summary
-- ---------------------------------------------------------------------------

ALTER TABLE public.artifact_cdg_bindings
    ADD COLUMN IF NOT EXISTS action_class public.heuristic_action_class DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS status public.cdg_binding_status NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- 5. RLS policies
-- ---------------------------------------------------------------------------

ALTER TABLE public.heuristic_registry ENABLE ROW LEVEL SECURITY;

CREATE POLICY select_heuristic_registry ON public.heuristic_registry
    FOR SELECT TO authenticated USING (true);

CREATE POLICY service_manage_heuristic_registry ON public.heuristic_registry
    FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.artifact_cdg_binding_evidence ENABLE ROW LEVEL SECURITY;

CREATE POLICY select_binding_evidence ON public.artifact_cdg_binding_evidence
    FOR SELECT TO authenticated USING (true);

CREATE POLICY service_manage_binding_evidence ON public.artifact_cdg_binding_evidence
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------------
-- 6. RPCs
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.get_heuristics_by_domain(
    request_domain TEXT,
    request_family TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT COALESCE(
        jsonb_agg(row_to_json(h)::jsonb ORDER BY h.heuristic_id),
        '[]'::jsonb
    )
    FROM public.heuristic_registry h
    WHERE h.domain = request_domain
      AND (request_family IS NULL OR h.family = request_family);
$$;

GRANT EXECUTE ON FUNCTION public.get_heuristics_by_domain(TEXT, TEXT)
    TO authenticated;

CREATE OR REPLACE FUNCTION public.get_binding_evidence(
    request_version_id UUID,
    request_node_id TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'binding_id', b.binding_id,
                'node_id', b.node_id,
                'bound_artifact_fqdn', b.bound_artifact_fqdn,
                'binding_confidence', b.binding_confidence,
                'binding_source', b.binding_source,
                'action_class', b.action_class,
                'status', b.status,
                'alternatives', b.alternatives,
                'evidence_summary', b.evidence_summary,
                'evidence', (
                    SELECT COALESCE(
                        jsonb_agg(
                            row_to_json(e)::jsonb ORDER BY e.heuristic_id
                        ),
                        '[]'::jsonb
                    )
                    FROM public.artifact_cdg_binding_evidence e
                    WHERE e.binding_id = b.binding_id
                )
            ) ORDER BY b.node_id
        ),
        '[]'::jsonb
    )
    FROM public.artifact_cdg_bindings b
    WHERE b.version_id = request_version_id
      AND (request_node_id IS NULL OR b.node_id = request_node_id);
$$;

GRANT EXECUTE ON FUNCTION public.get_binding_evidence(UUID, TEXT)
    TO authenticated;
