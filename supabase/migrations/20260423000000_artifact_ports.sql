-- Migration: artifact_ports
-- Purpose: Support GPU, C, WASM, and other platform-specific ports of atoms.
-- A port is a semantically equivalent implementation of an atom for a different
-- hardware target. The source atom is the canonical numpy/scipy version; ports
-- are alternative implementations that trade portability for performance.
--
-- Design rationale: Ports are a property of the ATOM, not of a CDG binding.
-- A dedicated table lets any CDG that uses the source atom discover available
-- ports via a single JOIN, enabling hardware-aware atom selection at execution
-- time and ESG compute-savings reporting.

-- Port kind enum: extensible as new targets emerge
CREATE TYPE public.port_kind AS ENUM (
    'gpu_cuda',
    'gpu_metal',
    'gpu_opencl',
    'c',
    'cpp',
    'wasm',
    'fpga',
    'tpu'
);

-- Ports table: links a source (canonical) atom to a port (optimized) atom
CREATE TABLE IF NOT EXISTS public.artifact_ports (
    port_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The canonical numpy/scipy atom
    source_artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id),

    -- The optimized port (a separate artifact with its own code, tests, etc.)
    port_artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id),

    -- What kind of port this is
    port_kind public.port_kind NOT NULL,

    -- Hardware requirements for the port (e.g., "CUDA 12+, compute capability 7.0+")
    device_requirements TEXT NOT NULL DEFAULT '',

    -- Measured performance characteristics (NULL = not yet benchmarked)
    speedup_factor DOUBLE PRECISION,          -- e.g., 4.7 means 4.7x faster than source
    memory_delta_bytes BIGINT,                -- positive = uses more memory than source
    latency_profile JSONB NOT NULL DEFAULT '{}'::jsonb,  -- detailed timing breakdown

    -- Attribution to upstream libraries used by the port
    attribution_notes TEXT NOT NULL DEFAULT '',

    -- Has this port been benchmarked against the source on matching inputs?
    verified BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A source atom can have at most one port per port_artifact
    UNIQUE (source_artifact_id, port_artifact_id),

    -- Prevent self-ports
    CHECK (source_artifact_id != port_artifact_id)
);

-- Indexes for common access patterns
CREATE INDEX idx_artifact_ports_source ON public.artifact_ports(source_artifact_id);
CREATE INDEX idx_artifact_ports_kind ON public.artifact_ports(port_kind);
CREATE INDEX idx_artifact_ports_verified ON public.artifact_ports(verified) WHERE verified = TRUE;

-- RPC: Get all available ports for an atom by FQDN
-- Used by the architect's binding resolution to discover hardware-optimized alternatives
CREATE OR REPLACE FUNCTION public.get_ports_for_atom(p_atom_fqdn TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'port_id', p.port_id,
        'port_fqdn', port_art.fqdn,
        'port_kind', p.port_kind,
        'device_requirements', p.device_requirements,
        'speedup_factor', p.speedup_factor,
        'memory_delta_bytes', p.memory_delta_bytes,
        'verified', p.verified,
        'attribution_notes', p.attribution_notes
    ) ORDER BY p.speedup_factor DESC NULLS LAST), '[]'::jsonb)
    FROM public.artifact_ports p
    JOIN public.artifacts source_art ON source_art.artifact_id = p.source_artifact_id
    JOIN public.artifacts port_art ON port_art.artifact_id = p.port_artifact_id
    WHERE source_art.fqdn = p_atom_fqdn
      AND source_art.status != 'blocked'
      AND port_art.status != 'blocked';
$$;

-- RPC: Get all ports of a given kind (useful for "show me all GPU atoms")
CREATE OR REPLACE FUNCTION public.get_ports_by_kind(p_kind public.port_kind)
RETURNS JSONB
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'source_fqdn', source_art.fqdn,
        'port_fqdn', port_art.fqdn,
        'port_kind', p.port_kind,
        'speedup_factor', p.speedup_factor,
        'verified', p.verified
    ) ORDER BY source_art.fqdn), '[]'::jsonb)
    FROM public.artifact_ports p
    JOIN public.artifacts source_art ON source_art.artifact_id = p.source_artifact_id
    JOIN public.artifacts port_art ON port_art.artifact_id = p.port_artifact_id
    WHERE p.port_kind = p_kind
      AND source_art.status != 'blocked'
      AND port_art.status != 'blocked';
$$;

-- Enable RLS (ports are public read, admin write)
ALTER TABLE public.artifact_ports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Ports are publicly readable"
    ON public.artifact_ports FOR SELECT
    USING (TRUE);

CREATE POLICY "Only service role can manage ports"
    ON public.artifact_ports FOR ALL
    USING (auth.role() = 'service_role');
