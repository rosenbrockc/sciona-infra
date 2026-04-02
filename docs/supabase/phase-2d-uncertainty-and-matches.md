# Phase 2D: Uncertainty Estimates, Verification Matches & Behavioral Matching Tables

## Overview

Phase 2D creates and populates five tables covering pipeline-produced scientific
metadata about atoms: empirical uncertainty characterizations, formal
verification match artifacts, and behavioral equivalence / fuzzing results from
the Phase D ecosystem migration (004).

| Table | Source | Approximate count |
|---|---|---|
| `atom_uncertainty_estimates` | `**/uncertainty.json` (22 files in `ageo-atoms/ageoa/`) | ~22 files, ~1 estimate row each |
| `atom_verification_matches` | `**/matches.json` (83 files in `ageo-atoms/ageoa/`) | ~83 files, multiple rows each |
| `fuzz_results` | Phase D migration 004 (`migrations/004_phase_d_ecosystem.sql`) | Initially empty; populated by pipeline |
| `behavioral_equivalence_flags` | Phase D migration 004 (`migrations/004_phase_d_ecosystem.sql`) | Initially empty; populated by pipeline |

Additionally, existing `fuzz_results` rows are cross-migrated into
`atom_audit_evidence` as `audit_type = 'fuzz_test'` rows for unified audit
querying (see Section 2.5).

### Dependencies

- **Requires Phase 1** (core tables): `atoms`, `atom_versions`, `users` must
  exist with populated `atom_id` / `fqdn` / `user_id` columns.
- **Can run in parallel** with Phases 2A, 2B, 2C, 2E -- no cross-dependencies.

### Data file locations

Uncertainty and matches data lives alongside atom source code in
`ageo-atoms/ageoa/`. Files appear at two levels:

1. **Domain-level**: `ageoa/<domain>/uncertainty.json` and
   `ageoa/<domain>/matches.json` (e.g. `ageoa/astroflow/uncertainty.json`)
2. **Sub-module level**: `ageoa/<domain>/<sub>/uncertainty.json` and
   `ageoa/<domain>/<sub>/matches.json` (e.g.
   `ageoa/tempo_jl/tai2utc/matches.json`)
3. **Under `_artifacts/`**: `ageoa/<domain>/_artifacts/<sub>/uncertainty.json`
   (e.g. `ageoa/bayes_rs/_artifacts/bernoulli/uncertainty.json`)

The `fuzz_results` and `behavioral_equivalence_flags` tables have no file-based
backfill data -- they are populated by the running pipeline. Their DDL is
carried forward from `migrations/004_phase_d_ecosystem.sql`.

---

## 1. SQL DDL

> **REFERENCE ONLY** — All tables, indexes, and RLS policies below are already
> created in **Phase 0** (tables + indexes) and **Phase 3** (RLS policies). Do NOT
> execute the DDL or RLS statements in this section. They are included for
> documentation. **Skip directly to Section 2 (Backfill Scripts) when executing
> this phase.**

### 1.1 `atom_uncertainty_estimates`

Empirical sensitivity characterizations. Each row records how sensitive an
atom's output is to input perturbations under a specific input regime.

```sql
-- ============================================================
-- ATOM UNCERTAINTY ESTIMATES
-- ============================================================

CREATE TABLE public.atom_uncertainty_estimates (
    estimate_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id    UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    mode          TEXT NOT NULL DEFAULT 'empirical'
                  CHECK (mode IN ('empirical', 'analytical', 'propagated')),
    scalar_factor DOUBLE PRECISION NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    n_trials      INTEGER NOT NULL DEFAULT 0,
    epsilon       DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_regime  TEXT NOT NULL DEFAULT '',    -- e.g. "shape=(1000,), dtype=float64"
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_uncertainty_atom ON public.atom_uncertainty_estimates (atom_id);
CREATE INDEX idx_uncertainty_version ON public.atom_uncertainty_estimates (version_id);
```

### 1.2 `atom_verification_matches`

Pipeline-produced match artifacts from `matches.json`: proof terms, compiler
output, verification levels, candidate scores, and retrieval methods. These
record how the atom was matched against formal specifications during ingestion.

```sql
-- ============================================================
-- ATOM VERIFICATION MATCHES
-- ============================================================

CREATE TABLE public.atom_verification_matches (
    match_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,

    -- The formal predicate this atom was matched against
    predicate_id   TEXT NOT NULL DEFAULT '',
    predicate_statement TEXT NOT NULL DEFAULT '',
    informal_desc  TEXT NOT NULL DEFAULT '',

    -- Best verified match
    candidate_name TEXT NOT NULL DEFAULT '',
    candidate_source_lib TEXT NOT NULL DEFAULT '',
    candidate_score DOUBLE PRECISION,
    retrieval_method TEXT NOT NULL DEFAULT '',
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    verification_level TEXT NOT NULL DEFAULT 'unverified'
                   CHECK (verification_level IN ('kernel_proof', 'type_checked',
                                                  'contract_checked', 'unverified')),
    proof_term     TEXT NOT NULL DEFAULT '',
    compiler_output TEXT NOT NULL DEFAULT '',
    error_message  TEXT NOT NULL DEFAULT '',

    -- Full candidate/verification history (flexible, may be large)
    all_candidates JSONB NOT NULL DEFAULT '[]',
    all_verifications JSONB NOT NULL DEFAULT '[]',

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_verification_matches_atom ON public.atom_verification_matches (atom_id);
CREATE INDEX idx_verification_matches_version ON public.atom_verification_matches (version_id);
CREATE INDEX idx_verification_matches_level ON public.atom_verification_matches (verification_level);
```

### 1.3 `fuzz_results`

Carried forward from `migrations/004_phase_d_ecosystem.sql`. Records
property-based fuzzing, boundary-value testing, and behavioral equivalence
fuzzing results per atom version.

```sql
-- ============================================================
-- FUZZ RESULTS (from Phase D migration 004)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.fuzz_results (
    fuzz_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_fqdn       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    strategy        TEXT NOT NULL
                    CHECK (strategy IN ('property_based', 'boundary_value',
                                        'param_smoothing', 'behavioral_equiv')),
    passed          BOOLEAN NOT NULL,
    failures        JSONB DEFAULT '[]',
    inputs_tested   INTEGER NOT NULL DEFAULT 0,
    runtime_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fuzz_results_atom
    ON public.fuzz_results (atom_fqdn, content_hash);
```

**Cross-migration note**: Existing `fuzz_results` rows are also back-populated
into `atom_audit_evidence` with `audit_type = 'fuzz_test'` for unified audit
querying (see Section 2.5). The `fuzz_results` table is retained read-only for
backward compatibility with Phase D ecosystem queries. New fuzz runs should
write to `atom_audit_evidence`.

### 1.4 `behavioral_equivalence_flags`

Carried forward from `migrations/004_phase_d_ecosystem.sql`. Records pairwise
behavioral equivalence observations between atom versions, flagging cases of
potential plagiarism, coincidental similarity, or shared algorithm lineage.

```sql
-- ============================================================
-- BEHAVIORAL EQUIVALENCE FLAGS (from Phase D migration 004)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.behavioral_equivalence_flags (
    flag_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_a_fqdn     TEXT NOT NULL,
    atom_a_hash     TEXT NOT NULL,
    atom_b_fqdn     TEXT NOT NULL,
    atom_b_hash     TEXT NOT NULL,
    match_ratio     FLOAT NOT NULL,
    sample_size     INTEGER NOT NULL,
    reviewed        BOOLEAN DEFAULT FALSE,
    reviewer_id     UUID REFERENCES public.users(user_id),
    disposition     TEXT CHECK (disposition IS NULL OR
                    disposition IN ('plagiarism', 'coincidence', 'common_algorithm')),
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 1.5 Row-Level Security

All four tables have RLS enabled. Uncertainty estimates and verification
matches delegate visibility to the parent `atoms` row. Fuzz results and
behavioral equivalence flags are publicly readable (read-only ecosystem data).

```sql
ALTER TABLE public.atom_uncertainty_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_verification_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fuzz_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.behavioral_equivalence_flags ENABLE ROW LEVEL SECURITY;

-- Uncertainty estimates: follows atom visibility
CREATE POLICY uncertainty_estimates_select ON public.atom_uncertainty_estimates
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_uncertainty_estimates.atom_id
        )
    );

-- Verification matches: follows atom visibility
CREATE POLICY verification_matches_select ON public.atom_verification_matches
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_verification_matches.atom_id
        )
    );

-- Fuzz results: publicly readable
CREATE POLICY fuzz_results_select ON public.fuzz_results
    FOR SELECT USING (true);

-- Behavioral equivalence flags: readable by authenticated users
CREATE POLICY behavioral_equiv_select ON public.behavioral_equivalence_flags
    FOR SELECT TO authenticated USING (true);
```

---

## 2. Backfill Scripts

### 2.1 FQDN Resolution Strategy

Both `uncertainty.json` and `matches.json` use **bare function names** (not
FQDNs) as their atom identifiers. The `atom` field in `uncertainty.json` and
the `predicate_id` field in `matches.json` are short names like
`"dedispersionkernel"` or `"isleapyear"`, while the `atoms.fqdn` column stores
fully-qualified dotted names like `"ageoa.astroflow.dedispersionkernel"` or
`"ageoa.tempo_jl.isleapyear"`.

**Resolution algorithm** (shared by both backfill scripts):

1. **Derive namespace from file path**: Convert the file's directory path to a
   dotted namespace. Strip any `_artifacts` segment and everything after it.

   | File path | Derived namespace |
   |---|---|
   | `ageoa/astroflow/uncertainty.json` | `ageoa.astroflow` |
   | `ageoa/tempo_jl/tai2utc/matches.json` | `ageoa.tempo_jl.tai2utc` |
   | `ageoa/mint/_artifacts/apc_module/uncertainty.json` | `ageoa.mint` |
   | `ageoa/bayes_rs/_artifacts/bernoulli/uncertainty.json` | `ageoa.bayes_rs` |
   | `ageoa/rust_robotics/n_joint_arm_2d/matches.json` | `ageoa.rust_robotics.n_joint_arm_2d` |

2. **Construct candidate FQDN**: `{namespace}.{short_name}` -- e.g.
   `ageoa.astroflow.dedispersionkernel`.

3. **Primary lookup**: Exact match against `atoms.fqdn`.

4. **Fallback -- suffix match**: If exact match fails, query
   `atoms.fqdn LIKE '%.<short_name>'` with `LIMIT 1`. This handles cases where
   the namespace derivation from the file path doesn't exactly match the FQDN
   in the database (e.g. intermediate path segments that are not part of the
   canonical FQDN).

5. **Unresolved entries**: Log a warning and increment `skipped_no_atom`. These
   are expected for atoms that exist in `ageo-atoms` source but haven't been
   registered in the `atoms` table yet.

**Shared utility** (`scripts/backfill_utils.py`):

```python
from pathlib import Path

def namespace_from_path(file_path: Path) -> str:
    """Derive dotted namespace from file path.

    ageoa/pulsar_folding/uncertainty.json             -> ageoa.pulsar_folding
    ageoa/mint/_artifacts/apc_module/uncertainty.json  -> ageoa.mint
    ageoa/tempo_jl/tai2utc/matches.json               -> ageoa.tempo_jl.tai2utc
    """
    parts = file_path.parent.parts
    clean = []
    for p in parts:
        if p == "_artifacts":
            break
        clean.append(p)
    return ".".join(clean)


def resolve_atom_id(supabase, namespace: str, short_name: str) -> str | None:
    """Resolve an atom short name + namespace to an atom_id UUID."""
    fqdn = f"{namespace}.{short_name}"
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    # Fallback: suffix match
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .like("fqdn", f"%.{short_name}")
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    return None
```

### 2.2 Backfill Uncertainty Estimates

**Source file structure** (`uncertainty.json`):

```json
{
  "atom": "dedispersionkernel",
  "estimates": [
    {
      "mode": "empirical",
      "scalar_factor": 0.696465,
      "confidence": 0.9,
      "n_trials": 500,
      "epsilon": 1e-08,
      "input_regime": "multi-arg perturbation, shapes={'input_data': '(256,)', 'delay_table': '(256,)'}",
      "notes": "perturbation harness, 500/500 trials, perturbed params: ['input_data', 'delay_table']"
    }
  ]
}
```

All 22 observed files follow this exact structure. The `estimates` array always
contains a single entry in current data, but the script handles multiple. The
`atom` field is a bare function name requiring FQDN resolution.

**Observed data characteristics**:

- `scalar_factor` ranges from ~0.5 to ~34.5 (most between 0.5 and 1.2)
- `confidence` is always 0.9 except for high-variance atoms (reduced to 0.3
  when `scalar_factor > 10`, as noted in the `notes` field)
- `n_trials` is always 500
- `epsilon` is always 1e-08
- `mode` is always `"empirical"` (generated by `scripts/measure_uncertainty.py`)

**Field mapping**:

| JSON field | Column | Notes |
|---|---|---|
| `atom` (resolved via Section 2.1) | `atom_id` | UUID from `atoms` table lookup |
| (none) | `version_id` | `NULL` for backfill (no version tracking in file) |
| `estimates[].mode` | `mode` | Always `'empirical'` in current data |
| `estimates[].scalar_factor` | `scalar_factor` | Required, `DOUBLE PRECISION` |
| `estimates[].confidence` | `confidence` | Required, range `[0, 1]` |
| `estimates[].n_trials` | `n_trials` | Default `0` |
| `estimates[].epsilon` | `epsilon` | Default `0` |
| `estimates[].input_regime` | `input_regime` | Default `''` |
| `estimates[].notes` | `notes` | Default `''` |

**Script** (`scripts/backfill_uncertainty.py`):

```python
#!/usr/bin/env python3
"""Backfill atom_uncertainty_estimates from uncertainty.json files."""

import json
import logging
from pathlib import Path

from supabase import create_client

logger = logging.getLogger(__name__)

ATOMS_ROOT = Path("ageoa")
BATCH_SIZE = 50


def namespace_from_path(file_path: Path) -> str:
    """Derive dotted namespace from file path, stripping _artifacts."""
    parts = file_path.parent.parts
    clean = []
    for p in parts:
        if p == "_artifacts":
            break
        clean.append(p)
    return ".".join(clean)


def resolve_atom_id(supabase, namespace: str, short_name: str) -> str | None:
    """Resolve an atom short name + namespace to an atom_id UUID."""
    fqdn = f"{namespace}.{short_name}"
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    # Fallback: suffix match
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .like("fqdn", f"%.{short_name}")
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    return None


def backfill_uncertainty(supabase) -> dict:
    stats = {"found": 0, "inserted": 0, "skipped_no_atom": 0, "errors": 0}
    batch = []

    for unc_path in ATOMS_ROOT.rglob("uncertainty.json"):
        stats["found"] += 1
        try:
            data = json.loads(unc_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", unc_path, exc)
            stats["errors"] += 1
            continue

        atom_name = data.get("atom", "")
        if not atom_name:
            logger.warning("No atom name in %s", unc_path)
            stats["errors"] += 1
            continue

        namespace = namespace_from_path(unc_path)
        atom_id = resolve_atom_id(supabase, namespace, atom_name)
        if not atom_id:
            logger.warning(
                "Atom not found: %s.%s (from %s)", namespace, atom_name, unc_path
            )
            stats["skipped_no_atom"] += 1
            continue

        for est in data.get("estimates", []):
            row = {
                "atom_id": atom_id,
                "version_id": None,
                "mode": est.get("mode", "empirical"),
                "scalar_factor": est["scalar_factor"],
                "confidence": est["confidence"],
                "n_trials": est.get("n_trials", 0),
                "epsilon": est.get("epsilon", 0),
                "input_regime": est.get("input_regime", ""),
                "notes": est.get("notes", ""),
            }
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                supabase.table("atom_uncertainty_estimates").insert(batch).execute()
                stats["inserted"] += len(batch)
                batch = []

    if batch:
        supabase.table("atom_uncertainty_estimates").insert(batch).execute()
        stats["inserted"] += len(batch)

    return stats


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    result = backfill_uncertainty(client)
    logger.info("Uncertainty backfill complete: %s", result)
```

### 2.3 Backfill Verification Matches

**Source file structure** (`matches.json`):

Each file is a JSON array of match entries. Each entry contains a `pdg_node`
(the formal predicate specification), a `verified_match` (the best candidate
match and its verification result), and arrays of all candidates and all
verification attempts.

```json
[
  {
    "pdg_node": {
      "predicate_id": "isleapyear",
      "statement": "(year: Any) -> Any",
      "informal_desc": "",
      "prover": "python",
      "context": {}
    },
    "verified_match": {
      "candidate": {
        "declaration": {
          "name": "isleapyear",
          "type_signature": "(year: Any) -> Any",
          "docstring": "",
          "conceptual_summary": "",
          "source_lib": "ingester",
          "prover": "python",
          "raw_code": ""
        },
        "score": 1.0,
        "retrieval_method": "ingester"
      },
      "verified": true,
      "compiler_output": "",
      "proof_term": "",
      "error_message": "",
      "verification_level": "type_checked"
    },
    "all_candidates": [ ... ],
    "all_verifications": [ ... ]
  }
]
```

The `predicate_id` field is a bare function name (e.g. `"isleapyear"`) used
for FQDN resolution. The match file's directory path provides the namespace
context. Some match files contain entries with rich `informal_desc` and
`conceptual_summary` fields; others leave them empty (typically earlier
ingestion runs vs. newer ones).

**FQDN resolution for matches**: The `predicate_id` field in each entry serves
as the atom short name. Combined with the directory-derived namespace, the
resolution follows the same algorithm as Section 2.1. For example:

| File path | `predicate_id` | Candidate FQDN |
|---|---|---|
| `ageoa/tempo_jl/tai2utc/matches.json` | `isleapyear` | `ageoa.tempo_jl.tai2utc.isleapyear` |
| `ageoa/biosppy/matches.json` | `validate_required_signal` | `ageoa.biosppy.validate_required_signal` |
| `ageoa/mcmc_foundational/mini_mcmc/hmc_llm/matches.json` | `initializehmckernelstate` | `ageoa.mcmc_foundational.mini_mcmc.hmc_llm.initializehmckernelstate` |

**Field mapping**:

| JSON path | Column | Notes |
|---|---|---|
| `pdg_node.predicate_id` (resolved) | `atom_id` | UUID from `atoms` table lookup |
| (none) | `version_id` | `NULL` for backfill |
| `pdg_node.predicate_id` | `predicate_id` | Text identifier |
| `pdg_node.statement` | `predicate_statement` | Type signature / spec |
| `pdg_node.informal_desc` | `informal_desc` | Human-readable description |
| `verified_match.candidate.declaration.name` | `candidate_name` | Best match name |
| `verified_match.candidate.declaration.source_lib` | `candidate_source_lib` | e.g. `"ingester"` |
| `verified_match.candidate.score` | `candidate_score` | Float, nullable |
| `verified_match.candidate.retrieval_method` | `retrieval_method` | e.g. `"ingester"` |
| `verified_match.verified` | `verified` | Boolean |
| `verified_match.verification_level` | `verification_level` | Enum string |
| `verified_match.proof_term` | `proof_term` | May be empty |
| `verified_match.compiler_output` | `compiler_output` | May be empty |
| `verified_match.error_message` | `error_message` | May be empty |
| `all_candidates` | `all_candidates` | JSONB array (stored verbatim) |
| `all_verifications` | `all_verifications` | JSONB array (stored verbatim) |

**Script** (`scripts/backfill_verification_matches.py`):

```python
#!/usr/bin/env python3
"""Backfill atom_verification_matches from matches.json files."""

import json
import logging
from pathlib import Path

from supabase import create_client

logger = logging.getLogger(__name__)

ATOMS_ROOT = Path("ageoa")
BATCH_SIZE = 50


def namespace_from_path(file_path: Path) -> str:
    """Derive dotted namespace from file path, stripping _artifacts."""
    parts = file_path.parent.parts
    clean = []
    for p in parts:
        if p == "_artifacts":
            break
        clean.append(p)
    return ".".join(clean)


def resolve_atom_id(supabase, namespace: str, short_name: str) -> str | None:
    """Resolve an atom short name + namespace to an atom_id UUID."""
    fqdn = f"{namespace}.{short_name}"
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    # Fallback: suffix match
    resp = (
        supabase.table("atoms")
        .select("atom_id")
        .like("fqdn", f"%.{short_name}")
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["atom_id"]

    return None


def backfill_verification_matches(supabase) -> dict:
    stats = {
        "files_found": 0,
        "entries_found": 0,
        "inserted": 0,
        "skipped_no_atom": 0,
        "errors": 0,
    }
    batch = []

    for match_path in ATOMS_ROOT.rglob("matches.json"):
        stats["files_found"] += 1
        try:
            entries = json.loads(match_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", match_path, exc)
            stats["errors"] += 1
            continue

        if not isinstance(entries, list):
            logger.warning("Expected array in %s, got %s", match_path, type(entries))
            stats["errors"] += 1
            continue

        namespace = namespace_from_path(match_path)

        for match_result in entries:
            stats["entries_found"] += 1
            pdg_node = match_result.get("pdg_node", {})
            predicate_id = pdg_node.get("predicate_id", "")

            if not predicate_id:
                logger.warning("No predicate_id in entry from %s", match_path)
                stats["errors"] += 1
                continue

            atom_id = resolve_atom_id(supabase, namespace, predicate_id)
            if not atom_id:
                logger.warning(
                    "Atom not found: %s.%s (from %s)",
                    namespace,
                    predicate_id,
                    match_path,
                )
                stats["skipped_no_atom"] += 1
                continue

            verified_match = match_result.get("verified_match")
            candidate = (
                verified_match.get("candidate", {}) if verified_match else {}
            )
            decl = candidate.get("declaration", {}) if candidate else {}

            verification_level = (
                verified_match.get("verification_level", "unverified")
                if verified_match
                else "unverified"
            )
            # Guard against unexpected values
            valid_levels = {
                "kernel_proof",
                "type_checked",
                "contract_checked",
                "unverified",
            }
            if verification_level not in valid_levels:
                logger.warning(
                    "Unknown verification_level %r for %s, defaulting to unverified",
                    verification_level,
                    predicate_id,
                )
                verification_level = "unverified"

            row = {
                "atom_id": atom_id,
                "version_id": None,
                "predicate_id": predicate_id,
                "predicate_statement": pdg_node.get("statement", ""),
                "informal_desc": pdg_node.get("informal_desc", ""),
                "candidate_name": decl.get("name", ""),
                "candidate_source_lib": decl.get("source_lib", ""),
                "candidate_score": candidate.get("score"),
                "retrieval_method": candidate.get("retrieval_method", ""),
                "verified": (
                    verified_match.get("verified", False)
                    if verified_match
                    else False
                ),
                "verification_level": verification_level,
                "proof_term": (
                    verified_match.get("proof_term", "")
                    if verified_match
                    else ""
                ),
                "compiler_output": (
                    verified_match.get("compiler_output", "")
                    if verified_match
                    else ""
                ),
                "error_message": (
                    verified_match.get("error_message", "")
                    if verified_match
                    else ""
                ),
                "all_candidates": match_result.get("all_candidates", []),
                "all_verifications": match_result.get("all_verifications", []),
            }
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                supabase.table("atom_verification_matches").insert(batch).execute()
                stats["inserted"] += len(batch)
                batch = []

    if batch:
        supabase.table("atom_verification_matches").insert(batch).execute()
        stats["inserted"] += len(batch)

    return stats


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    result = backfill_verification_matches(client)
    logger.info("Verification matches backfill complete: %s", result)
```

### 2.4 `fuzz_results` and `behavioral_equivalence_flags` -- No File Backfill

These two tables are pipeline-populated at runtime and have no file-based
backfill source. Their DDL (Section 1.3, 1.4) is applied during schema
creation. The tables start empty and are filled by:

- **`fuzz_results`**: The fuzzing pipeline writes rows when it runs
  property-based, boundary-value, parameter-smoothing, or behavioral-equivalence
  fuzzing against atom versions.
- **`behavioral_equivalence_flags`**: The parity-check pipeline writes rows when
  it detects behavioral similarity between atom pairs above a configurable
  `match_ratio` threshold.

### 2.5 Cross-Migration: `fuzz_results` into `atom_audit_evidence`

Once `fuzz_results` accumulates rows (either from pipeline runs or a future
data import), they should be cross-migrated into the unified audit evidence
table for consistent querying. This runs after Phase 2B (audit tables) is
complete.

```sql
INSERT INTO public.atom_audit_evidence (
    atom_id, audit_type, passed, details, source_kind, runner_version, created_at
)
SELECT
    a.atom_id,
    'fuzz_test',
    fr.passed,
    jsonb_build_object(
        'strategy', fr.strategy,
        'inputs_tested', fr.inputs_tested,
        'failures', fr.failures,
        'runtime_ms', fr.runtime_ms
    ),
    'automated',
    'backfill-from-fuzz_results',
    fr.created_at
FROM public.fuzz_results fr
JOIN public.atoms a ON a.fqdn = fr.atom_fqdn;
```

After cross-migration, the `fuzz_results` table becomes read-only. New fuzz
runs should write directly to `atom_audit_evidence` with
`audit_type = 'fuzz_test'`.

---

## 3. Validation Queries

### 3.1 Schema Validation

Run after DDL migration:

```sql
-- Verify tables exist with correct columns
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'atom_uncertainty_estimates'
ORDER BY ordinal_position;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'atom_verification_matches'
ORDER BY ordinal_position;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'fuzz_results'
ORDER BY ordinal_position;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'behavioral_equivalence_flags'
ORDER BY ordinal_position;

-- Verify CHECK constraints
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.atom_uncertainty_estimates'::regclass
  AND contype = 'c';

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.atom_verification_matches'::regclass
  AND contype = 'c';

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.fuzz_results'::regclass
  AND contype = 'c';

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.behavioral_equivalence_flags'::regclass
  AND contype = 'c';

-- Verify indexes exist
SELECT indexname FROM pg_indexes
WHERE tablename IN (
    'atom_uncertainty_estimates',
    'atom_verification_matches',
    'fuzz_results',
    'behavioral_equivalence_flags'
);

-- Verify RLS is enabled
SELECT tablename, rowsecurity
FROM pg_tables
WHERE tablename IN (
    'atom_uncertainty_estimates',
    'atom_verification_matches',
    'fuzz_results',
    'behavioral_equivalence_flags'
);

-- Verify RLS policies exist
SELECT policyname, tablename
FROM pg_policies
WHERE tablename IN (
    'atom_uncertainty_estimates',
    'atom_verification_matches',
    'fuzz_results',
    'behavioral_equivalence_flags'
);
```

### 3.2 Backfill Validation (Uncertainty + Matches)

After running backfill scripts:

```sql
-- Row counts should match file counts
-- Uncertainty: ~22 files, ~22 rows (one estimate per file)
SELECT count(*) AS uncertainty_count FROM public.atom_uncertainty_estimates;

-- Verification matches: ~83 files, many rows per file
SELECT count(*) AS match_count FROM public.atom_verification_matches;

-- No orphaned rows (every row references a valid atom)
SELECT count(*) FROM public.atom_uncertainty_estimates ue
WHERE NOT EXISTS (
    SELECT 1 FROM public.atoms a WHERE a.atom_id = ue.atom_id
);
-- Expected: 0

SELECT count(*) FROM public.atom_verification_matches vm
WHERE NOT EXISTS (
    SELECT 1 FROM public.atoms a WHERE a.atom_id = vm.atom_id
);
-- Expected: 0

-- Confidence values in valid range
SELECT count(*) FROM public.atom_uncertainty_estimates
WHERE confidence < 0 OR confidence > 1;
-- Expected: 0

-- All verification_level values are valid enum members
SELECT DISTINCT verification_level, count(*)
FROM public.atom_verification_matches
GROUP BY verification_level;
-- Expected: only 'kernel_proof', 'type_checked', 'contract_checked', 'unverified'

-- Spot-check: known atom has expected uncertainty
SELECT a.fqdn, ue.scalar_factor, ue.confidence, ue.n_trials
FROM public.atom_uncertainty_estimates ue
JOIN public.atoms a ON a.atom_id = ue.atom_id
WHERE a.fqdn LIKE '%dedispersionkernel'
LIMIT 5;
-- Expected: scalar_factor ~0.696465, confidence 0.9, n_trials 500

-- Spot-check: known atom has verification match
SELECT a.fqdn, vm.predicate_id, vm.verification_level, vm.verified
FROM public.atom_verification_matches vm
JOIN public.atoms a ON a.atom_id = vm.atom_id
WHERE a.fqdn LIKE '%isleapyear'
LIMIT 5;
-- Expected: verification_level = 'type_checked', verified = true

-- Distribution of scalar_factor values (sanity check)
SELECT
    count(*) AS total,
    min(scalar_factor) AS min_sf,
    max(scalar_factor) AS max_sf,
    avg(scalar_factor) AS avg_sf
FROM public.atom_uncertainty_estimates;
-- Expected: min ~0.5, max ~34.5, avg ~2-4
```

### 3.3 Fuzz Results and Behavioral Equivalence Validation

After the pipeline populates these tables:

```sql
-- fuzz_results: verify strategy distribution
SELECT strategy, count(*), avg(inputs_tested)
FROM public.fuzz_results
GROUP BY strategy;

-- fuzz_results: verify all atom_fqdn values match existing atoms
SELECT fr.atom_fqdn, count(*)
FROM public.fuzz_results fr
LEFT JOIN public.atoms a ON a.fqdn = fr.atom_fqdn
WHERE a.atom_id IS NULL
GROUP BY fr.atom_fqdn;
-- Expected: 0 rows (all FQDNs resolve)

-- behavioral_equivalence_flags: verify match_ratio is in [0, 1]
SELECT count(*) FROM public.behavioral_equivalence_flags
WHERE match_ratio < 0 OR match_ratio > 1;
-- Expected: 0

-- behavioral_equivalence_flags: verify disposition values
SELECT disposition, count(*)
FROM public.behavioral_equivalence_flags
WHERE disposition IS NOT NULL
GROUP BY disposition;
-- Expected: only 'plagiarism', 'coincidence', 'common_algorithm'

-- Cross-migration validation: fuzz_results -> atom_audit_evidence
SELECT count(*) AS fuzz_in_evidence
FROM public.atom_audit_evidence
WHERE audit_type = 'fuzz_test'
  AND runner_version = 'backfill-from-fuzz_results';
-- Expected: matches count(*) from fuzz_results (for resolved atoms)
```

### 3.4 RLS Validation

```sql
-- As an authenticated user with general tier, verify visibility follows atoms
-- (requires test harness that sets auth.uid() to a known user)
SET request.jwt.claims = '{"sub": "<test-user-uuid>"}';

SELECT count(*) FROM public.atom_uncertainty_estimates;
-- Should return rows only for atoms the user can see

SELECT count(*) FROM public.atom_verification_matches;
-- Should return rows only for atoms the user can see

SELECT count(*) FROM public.fuzz_results;
-- Should return all rows (public read)

SELECT count(*) FROM public.behavioral_equivalence_flags;
-- Should return all rows (authenticated read)
```

---

## 4. Execution Order

1. **Run DDL** (Sections 1.1 -- 1.4): Create all four tables.
2. **Enable RLS** (Section 1.5): Enable RLS and attach SELECT policies.
3. **Backfill uncertainty** (Section 2.2): Run `backfill_uncertainty.py`.
4. **Backfill verification matches** (Section 2.3): Run
   `backfill_verification_matches.py`.
5. **Validate schema** (Section 3.1): Run schema validation queries.
6. **Validate backfill** (Section 3.2): Run backfill validation queries.
7. **Cross-migrate fuzz_results** (Section 2.5): Run after Phase 2B is complete
   and `fuzz_results` has been populated by pipeline runs.
8. **Validate fuzz/behavioral** (Section 3.3): Run after pipeline populates data.

Steps 3 and 4 can run in parallel since they target different tables.

---

## 5. Rollback Procedure

Rollback drops all four tables. Since `atom_uncertainty_estimates` and
`atom_verification_matches` are not referenced by foreign keys from other
tables, and `fuzz_results` and `behavioral_equivalence_flags` are standalone,
the order between them does not matter.

```sql
-- Drop RLS policies first
DROP POLICY IF EXISTS uncertainty_estimates_select ON public.atom_uncertainty_estimates;
DROP POLICY IF EXISTS verification_matches_select ON public.atom_verification_matches;
DROP POLICY IF EXISTS fuzz_results_select ON public.fuzz_results;
DROP POLICY IF EXISTS behavioral_equiv_select ON public.behavioral_equivalence_flags;

-- Drop tables
DROP TABLE IF EXISTS public.atom_verification_matches;
DROP TABLE IF EXISTS public.atom_uncertainty_estimates;
DROP TABLE IF EXISTS public.fuzz_results;
DROP TABLE IF EXISTS public.behavioral_equivalence_flags;
```

No other tables or views reference these four tables, so dropping them has no
side effects beyond removing the data. The `get_atom_documentation` RPC
(Section 2.8 of the migration plan) includes subqueries against the uncertainty
and match tables, but that RPC is created in a later phase and handles missing
tables gracefully via `LEFT JOIN` / subquery returning `NULL`.

**Backfill-only rollback** (if DDL is fine but backfill data is wrong):

```sql
TRUNCATE public.atom_uncertainty_estimates;
TRUNCATE public.atom_verification_matches;
-- fuzz_results and behavioral_equivalence_flags: only truncate if you want
-- to discard pipeline-produced data (not file-backfilled)
TRUNCATE public.fuzz_results;
TRUNCATE public.behavioral_equivalence_flags;
```

Then re-run the backfill scripts and/or pipeline jobs.

---

## 6. Notes

### 6.1 Files in `_artifacts/` subdirectories

Some `uncertainty.json` files live under `_artifacts/` subdirectories (e.g.
`ageoa/mint/_artifacts/apc_module/uncertainty.json`). The `namespace_from_path`
function strips `_artifacts` and everything after it, producing the correct
parent namespace (e.g. `ageoa.mint`). The atom short name inside the JSON is
used for the final lookup segment.

### 6.2 Duplicate predicate entries in `matches.json`

A single `matches.json` can contain multiple entries with the same
`predicate_id` but different type signatures (e.g. overloaded atoms). Each
entry becomes a separate row in `atom_verification_matches`. They share the
same `atom_id` but differ in `predicate_statement` and match details.

### 6.3 `version_id` is NULL for backfill

The source JSON files do not contain version information. All backfilled rows
have `version_id = NULL`. Future pipeline runs that produce these files should
populate `version_id` when inserting directly to Supabase.

### 6.4 Large JSONB columns

The `all_candidates` and `all_verifications` JSONB columns can be large when
atoms have many candidate matches. This is acceptable for the current dataset
size. If row sizes become problematic, consider extracting these into separate
normalized tables in a future phase.

### 6.5 `fuzz_results` uses `atom_fqdn` (TEXT), not `atom_id` (UUID)

The `fuzz_results` table references atoms by FQDN string rather than UUID
foreign key. This is carried forward from the original Phase D migration for
backward compatibility. The cross-migration query (Section 2.5) performs the
JOIN via `atoms.fqdn = fuzz_results.atom_fqdn`. If a `fuzz_results.atom_fqdn`
value does not match any row in `atoms`, that fuzz result will be silently
skipped during cross-migration.

### 6.6 `behavioral_equivalence_flags` is pairwise

Each row compares two specific atom versions (identified by FQDN + content
hash). The `disposition` column is nullable -- it starts as `NULL` until a
reviewer classifies the flag. The `reviewer_id` references `users(user_id)`
and is also nullable for unreviewed flags.

### 6.7 Relationship between `fuzz_results` strategies and `behavioral_equivalence_flags`

When `fuzz_results.strategy = 'behavioral_equiv'`, the fuzz run was
specifically testing behavioral equivalence between atoms. The
`behavioral_equivalence_flags` table captures the *flagged results* of such
runs (and potentially other detection methods). They are complementary: fuzz
results record the test execution; equivalence flags record the discovered
relationships.
