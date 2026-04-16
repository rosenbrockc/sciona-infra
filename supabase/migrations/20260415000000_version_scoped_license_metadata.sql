-- Version-scoped license metadata for atom and artifact versions.
-- Additive only: no enforcement triggers or public policies yet.

CREATE TABLE IF NOT EXISTS public.atom_version_license_metadata (
    version_license_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES public.atom_versions(version_id) ON DELETE CASCADE,
    license_expression TEXT NOT NULL DEFAULT '',
    license_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_status IN ('approved', 'restricted', 'unknown', 'needs_legal_review')),
    license_family TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_family IN ('permissive', 'weak_copyleft', 'strong_copyleft', 'proprietary', 'unknown')),
    license_source_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_source_kind IN (
            'repo_root_license',
            'per_atom_manifest',
            'upstream_vendor_license',
            'manual_override',
            'pyproject',
            'license_file',
            'unknown'
        )),
    license_source_path TEXT NOT NULL DEFAULT '',
    upstream_license_expression TEXT NOT NULL DEFAULT '',
    license_confidence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_confidence IN ('low', 'medium', 'high', 'unknown')),
    license_notes TEXT NOT NULL DEFAULT '',
    normalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id)
);

CREATE INDEX IF NOT EXISTS idx_atom_version_license_metadata_atom
    ON public.atom_version_license_metadata (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_version_license_metadata_version
    ON public.atom_version_license_metadata (version_id);

CREATE TABLE IF NOT EXISTS public.artifact_version_license_metadata (
    version_license_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id) ON DELETE CASCADE,
    license_expression TEXT NOT NULL DEFAULT '',
    license_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_status IN ('approved', 'restricted', 'unknown', 'needs_legal_review')),
    license_family TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_family IN ('permissive', 'weak_copyleft', 'strong_copyleft', 'proprietary', 'unknown')),
    license_source_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_source_kind IN (
            'repo_root_license',
            'per_atom_manifest',
            'upstream_vendor_license',
            'manual_override',
            'pyproject',
            'license_file',
            'unknown'
        )),
    license_source_path TEXT NOT NULL DEFAULT '',
    upstream_license_expression TEXT NOT NULL DEFAULT '',
    license_confidence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_confidence IN ('low', 'medium', 'high', 'unknown')),
    license_notes TEXT NOT NULL DEFAULT '',
    normalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_version_license_metadata_artifact
    ON public.artifact_version_license_metadata (artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_version_license_metadata_version
    ON public.artifact_version_license_metadata (version_id);
