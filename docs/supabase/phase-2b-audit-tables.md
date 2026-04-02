# Phase 2B: Audit & Verification Tables

## Overview

Phase 2B creates the full audit and verification storage model, then backfills it
from two primary sources:

1. `audit_manifest.json` (505 atoms of audit evidence and rollup data)
2. The existing `fuzz_results` table (legacy ecosystem rows from Phase D)
3. Existing verification data in the old PG database (bounty verification runs,
   budgets, and best scores)

The phase covers six database objects spanning two logical domains:

### Audit domain (three-layer model)

| Layer | Object | Purpose |
|-------|--------|---------|
| Raw evidence | `atom_audit_evidence` table | Append-only log of every audit run |
| Latest per-type | `atom_audit_latest` materialized view | Most recent result per (atom, audit_type) |
| Published rollup | `atom_audit_rollups` table | Single canonical row per atom for serving/ranking |

### Verification domain (Phase C carry-forward)

| Object | Purpose |
|--------|---------|
| `verification_runs` | Individual verification executions against submissions |
| `verification_budgets` | Per-bounty verification slot allocation and cost tracking |
| `bounty_best_scores` | Best-to-date metric tracking per bounty |

### Design: raw evidence vs. computed rollups vs. materialized latest

The three-layer audit model is the most important structural decision in the audit
domain. It separates concerns cleanly:

1. **Raw evidence** (`atom_audit_evidence`): Append-only. Every audit tool writes here.
   Multiple evidence rows per atom per audit type over time. Never updated or deleted
   during normal operations. Each row records the result of a specific audit run,
   including the tool version, source revision, and type-specific details in JSONB.

2. **Computed rollups** (`atom_audit_rollups`): One row per atom. Computed by a rollup
   job that reads the latest evidence per type and aggregates into a serving-friendly
   row. This is what the served catalog reads. It is a **derived** table -- if lost,
   it can be recomputed from evidence. Updated via upsert, not append.

3. **Materialized latest view** (`atom_audit_latest`): A `DISTINCT ON` materialized view
   over evidence, giving the most recent row per (atom_id, audit_type). Used by the
   rollup computation and by the `get_atom_document()` RPC to show the latest result
   per audit category. Refreshed after audit runs via `REFRESH MATERIALIZED VIEW
   CONCURRENTLY`.

This separation means:
- Audit workers never touch the rollup table directly -- they only append evidence.
- A separate rollup job reads evidence and writes rollups.
- The served catalog reads rollups (fast, single row per atom).
- Full audit history is always preserved in the evidence table.

### Dependencies

- **Requires Phase 1** (schema provisioning): `public.atoms`, `public.atom_versions`,
  `public.bounties`, and `public.submissions` must exist because the audit and
  verification tables reference them via foreign keys.
- **Requires Phase 2A atoms backfill**: The atoms table must be populated before the
  audit backfill scripts can resolve `fqdn -> atom_id`.

### Parallelism

Phase 2B can run in parallel with Phases 2C (References), 2D (Descriptions), and 2E
(IO Specs / Parameters) because none of those phases read or write the audit or
verification tables.

---

## Step 1: Create `atom_audit_evidence` Table

> **REFERENCE ONLY** — All tables, indexes, and RLS policies below are already
> created in **Phase 0** (with triggers DISABLED) and **Phase 3** (RLS). Do NOT
> execute the DDL or RLS statements in this section. They are included for
> documentation. **Skip to the Backfill Scripts section when executing this phase.**

### DDL

```sql
-- ============================================================
-- ATOM AUDIT EVIDENCE
-- ============================================================

CREATE TABLE public.atom_audit_evidence (
    evidence_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,

    audit_type     TEXT NOT NULL
                   CHECK (audit_type IN (
                       'smoke_test',        -- basic import + call test
                       'regression_test',   -- output stability across versions
                       'structural_audit',  -- AST/contract/typing checks
                       'semantic_audit',    -- LLM-verified meaning preservation
                       'risk_assessment',   -- security/safety tier assignment
                       'parity_check',      -- cross-implementation equivalence
                       'fuzz_test'          -- property-based / boundary fuzzing
                   )),

    passed         BOOLEAN NOT NULL,
    status         TEXT NOT NULL DEFAULT 'completed'
                   CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),

    -- Type-dependent metadata in JSONB:
    -- smoke_test:       { "import_ok": bool, "call_ok": bool, "error": str|null }
    -- regression_test:  { "baseline_hash": str, "delta": float, "metric": str }
    -- structural_audit: { "contracts_present": bool, "typing_complete": bool, "issues": [...] }
    -- semantic_audit:   { "llm_model": str, "confidence": float, "evidence": str }
    -- risk_assessment:  { "risk_tier": "low"|"medium"|"high", "factors": [...] }
    -- parity_check:     { "reference_impl": str, "match_ratio": float, "sample_size": int }
    -- fuzz_test:        { "strategy": str, "inputs_tested": int, "failures": [...] }
    details        JSONB NOT NULL DEFAULT '{}',

    -- Provenance
    source_kind    TEXT NOT NULL DEFAULT 'automated'
                   CHECK (source_kind IN ('automated', 'manual', 'llm_assisted')),
    runner_version TEXT NOT NULL DEFAULT '',      -- e.g. 'ingest-v2.3.1'
    run_duration_ms INTEGER,
    source_revision TEXT NOT NULL DEFAULT '',
    upstream_version TEXT NOT NULL DEFAULT '',
    review_basis_at TIMESTAMPTZ,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_evidence_atom ON public.atom_audit_evidence (atom_id);
CREATE INDEX idx_audit_evidence_version ON public.atom_audit_evidence (version_id);
CREATE INDEX idx_audit_evidence_type ON public.atom_audit_evidence (audit_type);
CREATE INDEX idx_audit_evidence_atom_type ON public.atom_audit_evidence (atom_id, audit_type);
```

### RLS

```sql
ALTER TABLE public.atom_audit_evidence ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_evidence_select ON public.atom_audit_evidence
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_evidence.atom_id
        )
    );
```

Write access is restricted to the service role (no INSERT/UPDATE/DELETE policies for
`authenticated`). Audit workers run with the service key.

---

## Step 2: Create `atom_audit_latest` Materialized View

### DDL

```sql
CREATE MATERIALIZED VIEW public.atom_audit_latest AS
SELECT DISTINCT ON (atom_id, audit_type)
    atom_id,
    audit_type,
    passed,
    status,
    details,
    source_kind,
    runner_version,
    source_revision,
    upstream_version,
    created_at AS audited_at
FROM public.atom_audit_evidence
ORDER BY atom_id, audit_type, created_at DESC;

CREATE UNIQUE INDEX idx_audit_latest_pk
    ON public.atom_audit_latest (atom_id, audit_type);
```

The unique index enables `REFRESH MATERIALIZED VIEW CONCURRENTLY`, which avoids
locking readers during refresh.

---

## Step 3: Create `atom_audit_rollups` Table

### DDL

```sql
CREATE TABLE public.atom_audit_rollups (
    atom_id              UUID PRIMARY KEY REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    overall_verdict      TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (overall_verdict IN ('unknown', 'trusted', 'acceptable_with_limits',
                                                    'limited_acceptability', 'misleading', 'broken')),
    structural_status    TEXT NOT NULL DEFAULT 'unknown',
    runtime_status       TEXT NOT NULL DEFAULT 'unknown',
    semantic_status      TEXT NOT NULL DEFAULT 'unknown',
    developer_semantics_status TEXT NOT NULL DEFAULT 'unknown',
    risk_tier            TEXT NOT NULL DEFAULT 'medium'
                         CHECK (risk_tier IN ('low', 'medium', 'high')),
    risk_score           INTEGER NOT NULL DEFAULT 0,
    -- Per-dimension risk breakdown (structural, fidelity, evidence_gap,
    -- statefulness, generation, ffi, semantics_proxy)
    risk_dimensions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_reasons         TEXT[] NOT NULL DEFAULT '{}',
    acceptability_score  INTEGER NOT NULL DEFAULT 0,
    acceptability_band   TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (acceptability_band IN ('unknown', 'acceptable_with_limits',
                                                       'acceptable_with_limits_candidate',
                                                       'limited_acceptability')),
    -- Parity coverage
    parity_coverage_level TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (parity_coverage_level IN ('unknown', 'none', 'not_applicable',
                                                          'positive_path', 'positive_and_negative',
                                                          'parity_or_usage_equivalent')),
    parity_test_status   TEXT NOT NULL DEFAULT 'unknown',
    parity_fixture_count INTEGER NOT NULL DEFAULT 0,
    parity_case_count    INTEGER NOT NULL DEFAULT 0,
    -- Review state
    review_status        TEXT NOT NULL DEFAULT 'missing',
    review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
    review_limitations   TEXT[] NOT NULL DEFAULT '{}',
    review_required_actions TEXT[] NOT NULL DEFAULT '{}',
    -- Trust readiness
    trust_readiness      TEXT NOT NULL DEFAULT 'not_ready',
    trust_blockers       TEXT[] NOT NULL DEFAULT '{}',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### RLS

```sql
ALTER TABLE public.atom_audit_rollups ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_rollups_select ON public.atom_audit_rollups
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_audit_rollups.atom_id
        )
    );
```

---

## Step 4: Create Phase C Verification Tables

These tables are carried forward from `migrations/003_phase_c_verification.sql` with
schema-qualified names and RLS policies added.

### 4a: `verification_budgets`

```sql
-- ============================================================
-- VERIFICATION BUDGETS
-- ============================================================

CREATE TABLE public.verification_budgets (
    bounty_id       UUID PRIMARY KEY REFERENCES public.bounties(bounty_id),
    tier            TEXT NOT NULL CHECK (tier IN ('standard', 'heavy', 'gpu')),
    total_slots     INT NOT NULL,
    used_slots      INT NOT NULL DEFAULT 0,
    cost_per_extra  NUMERIC(10,2) NOT NULL,
    overhead_deposit NUMERIC(10,2) NOT NULL DEFAULT 0,
    overhead_used   NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4b: `verification_runs`

```sql
-- ============================================================
-- VERIFICATION RUNS
-- ============================================================

CREATE TABLE public.verification_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    submission_id   UUID NOT NULL REFERENCES public.submissions(submission_id),
    split_type      TEXT NOT NULL CHECK (split_type IN ('public', 'blind')),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metric_values   JSONB,
    output_hash     TEXT,
    execution_time_s FLOAT,
    peak_memory_bytes BIGINT,
    is_deterministic BOOLEAN,
    sandbox_job_id  TEXT,
    slot_consumed   BOOLEAN NOT NULL DEFAULT false,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_verification_runs_bounty
    ON public.verification_runs (bounty_id);
CREATE INDEX idx_verification_runs_submission
    ON public.verification_runs (submission_id);
```

### 4c: `bounty_best_scores`

```sql
-- ============================================================
-- BOUNTY BEST SCORES
-- ============================================================

CREATE TABLE public.bounty_best_scores (
    bounty_id       UUID NOT NULL REFERENCES public.bounties(bounty_id),
    metric_name     TEXT NOT NULL,
    best_value      FLOAT NOT NULL,
    best_submission_id UUID REFERENCES public.submissions(submission_id),
    is_baseline     BOOLEAN NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bounty_id, metric_name)
);
```

### 4d: Verification Table RLS

```sql
ALTER TABLE public.verification_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verification_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bounty_best_scores ENABLE ROW LEVEL SECURITY;

-- All three are readable by any authenticated user
CREATE POLICY verification_budgets_select ON public.verification_budgets
    FOR SELECT TO authenticated USING (true);

CREATE POLICY verification_runs_select ON public.verification_runs
    FOR SELECT TO authenticated USING (true);

CREATE POLICY bounty_best_scores_select ON public.bounty_best_scores
    FOR SELECT TO authenticated USING (true);

-- Write access is restricted to the service role (verification pipeline).
-- No INSERT/UPDATE/DELETE policies for authenticated users.
```

---

## Step 5: Backfill Audit Evidence from `audit_manifest.json`

For each atom in the manifest, create one evidence row per audit category that has
results. The manifest does not store individual audit runs; it stores the latest
result per category. We treat the backfill as a single synthetic run with
`runner_version = 'backfill-v1'`.

### Evidence type mapping

Each evidence row is derived from specific manifest fields:

| `audit_type` | Manifest trigger field | `passed` when | `details` JSONB contents |
|---|---|---|---|
| `structural_audit` | `structural_status` | `== 'pass'` | `{ "status": structural_status, "findings": structural_findings, "finding_details": structural_finding_details }` |
| `semantic_audit` | `semantic_status` | `== 'pass'` | `{ "status": semantic_status, "findings": semantic_findings, "finding_details": semantic_finding_details }` |
| `risk_assessment` | `risk_tier` | `== 'low'` | `{ "risk_tier": risk_tier, "risk_score": risk_score, "risk_dimensions": risk_dimensions, "risk_reasons": risk_reasons }` |
| `parity_check` | `parity_coverage_level` | `in ('positive_and_negative', 'parity_or_usage_equivalent')` | `{ "coverage_level": parity_coverage_level, "coverage_reasons": parity_coverage_reasons, "test_status": parity_test_status, "fixture_count": parity_fixture_count, "case_count": parity_case_count, "usage_test_coverage": usage_test_coverage }` |
| `smoke_test` | `runtime_status` | `== 'pass'` | `{ "status": runtime_status, "status_basis": status_basis.get("runtime", []) }` |

### Field mapping: `audit_manifest.json` atom entry to evidence rows

Source fields consumed per evidence type:

| Evidence type | Manifest fields read |
|---|---|
| `structural_audit` | `structural_status`, `structural_findings`, `structural_finding_details`, `source_revision`, `upstream_version` |
| `semantic_audit` | `semantic_status`, `semantic_findings`, `semantic_finding_details`, `source_revision`, `upstream_version` |
| `risk_assessment` | `risk_tier`, `risk_score`, `risk_dimensions` (nested: `structural_risk`, `fidelity_risk`, `evidence_gap_risk`, `statefulness_risk`, `generation_risk`, `ffi_risk`, `semantics_proxy_risk`), `risk_reasons`, `source_revision`, `upstream_version` |
| `parity_check` | `parity_coverage_level`, `parity_coverage_reasons`, `parity_test_status`, `parity_fixture_count`, `parity_case_count`, `usage_test_coverage`, `source_revision`, `upstream_version` |
| `smoke_test` | `runtime_status`, `status_basis.runtime`, `source_revision`, `upstream_version` |

### Skip conditions

- `semantic_status == 'unknown'` (403 atoms): no semantic_audit evidence created
- `runtime_status == 'not_applicable'` (403 atoms): no smoke_test evidence created
- `parity_coverage_level in ('unknown', 'none')`: no parity_check evidence created
- `developer_semantics_status == 'unknown'` (all 505 atoms): no evidence created; captured only in rollups

### Script

```python
#!/usr/bin/env python3
"""Backfill atom_audit_evidence from audit_manifest.json.

Requires: Phase 1 schema + Phase 2A atoms backfill completed.
Run with: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars.
"""
import json
import os
import sys

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

MANIFEST_PATH = os.environ.get(
    "AUDIT_MANIFEST_PATH",
    "../ageo-atoms/data/audit_manifest.json",
)

RUNNER_VERSION = "backfill-v1"
BATCH_SIZE = 50


def build_evidence_rows(atom_id: str, entry: dict) -> list[dict]:
    """Build evidence rows for a single atom from its manifest entry."""
    rows = []
    common = {
        "atom_id": atom_id,
        "source_kind": "automated",
        "runner_version": RUNNER_VERSION,
        "source_revision": entry.get("source_revision") or "",
        "upstream_version": entry.get("upstream_version") or "",
    }

    # 1. Structural audit
    structural = entry.get("structural_status")
    if structural is not None:
        rows.append({
            **common,
            "audit_type": "structural_audit",
            "passed": structural == "pass",
            "details": {
                "status": structural,
                "findings": entry.get("structural_findings", []),
                "finding_details": entry.get("structural_finding_details", []),
            },
        })

    # 2. Semantic audit
    semantic = entry.get("semantic_status")
    if semantic is not None and semantic != "unknown":
        rows.append({
            **common,
            "audit_type": "semantic_audit",
            "passed": semantic == "pass",
            "details": {
                "status": semantic,
                "findings": entry.get("semantic_findings", []),
                "finding_details": entry.get("semantic_finding_details", []),
            },
        })

    # 3. Risk assessment
    risk_tier = entry.get("risk_tier")
    if risk_tier is not None:
        rows.append({
            **common,
            "audit_type": "risk_assessment",
            "passed": risk_tier == "low",
            "details": {
                "risk_tier": risk_tier,
                "risk_score": entry.get("risk_score", 0),
                "risk_dimensions": entry.get("risk_dimensions", {}),
                "risk_reasons": entry.get("risk_reasons", []),
            },
        })

    # 4. Parity check
    parity = entry.get("parity_coverage_level")
    if parity is not None and parity not in ("unknown", "none"):
        passed_parity = parity in (
            "positive_and_negative",
            "parity_or_usage_equivalent",
        )
        rows.append({
            **common,
            "audit_type": "parity_check",
            "passed": passed_parity,
            "details": {
                "coverage_level": parity,
                "coverage_reasons": entry.get("parity_coverage_reasons", []),
                "test_status": entry.get("parity_test_status", "unknown"),
                "fixture_count": entry.get("parity_fixture_count", 0),
                "case_count": entry.get("parity_case_count", 0),
                "usage_test_coverage": entry.get("usage_test_coverage", ""),
            },
        })

    # 5. Smoke test (from runtime_status, only if not 'not_applicable')
    runtime = entry.get("runtime_status")
    if runtime is not None and runtime != "not_applicable":
        rows.append({
            **common,
            "audit_type": "smoke_test",
            "passed": runtime == "pass",
            "details": {
                "status": runtime,
                "status_basis": (entry.get("status_basis") or {}).get("runtime", []),
            },
        })

    return rows


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    atoms_list = manifest["atoms"]
    print(f"Processing {len(atoms_list)} atoms from manifest...")

    # Pre-fetch all atom fqdn -> atom_id mappings in one query
    all_atoms = supabase.table("atoms").select("atom_id, fqdn").execute()
    fqdn_to_id = {row["fqdn"]: row["atom_id"] for row in all_atoms.data}
    print(f"Found {len(fqdn_to_id)} atoms in database")

    evidence_batch = []
    skipped = 0
    total_evidence = 0

    for entry in atoms_list:
        fqdn = entry["atom_name"]
        atom_id = fqdn_to_id.get(fqdn)
        if atom_id is None:
            skipped += 1
            continue

        rows = build_evidence_rows(atom_id, entry)
        evidence_batch.extend(rows)

        if len(evidence_batch) >= BATCH_SIZE:
            supabase.table("atom_audit_evidence").insert(evidence_batch).execute()
            total_evidence += len(evidence_batch)
            evidence_batch = []

    # Flush remaining
    if evidence_batch:
        supabase.table("atom_audit_evidence").insert(evidence_batch).execute()
        total_evidence += len(evidence_batch)

    print(f"Inserted {total_evidence} evidence rows, skipped {skipped} unresolved atoms")


if __name__ == "__main__":
    main()
```

---

## Step 6: Backfill Audit Rollups from `audit_manifest.json`

Each atom gets exactly one rollup row. The field mapping from `audit_manifest.json`
atom entries to `atom_audit_rollups` columns is:

### Field mapping: `audit_manifest.json` -> `atom_audit_rollups`

| `atom_audit_rollups` column | Manifest field | Default if missing |
|---|---|---|
| `atom_id` | Resolved via `atom_name` -> `atoms.fqdn` lookup | (skip atom) |
| `overall_verdict` | `overall_verdict` | `'unknown'` |
| `structural_status` | `structural_status` | `'unknown'` |
| `runtime_status` | `runtime_status` | `'unknown'` |
| `semantic_status` | `semantic_status` | `'unknown'` |
| `developer_semantics_status` | `developer_semantics_status` | `'unknown'` |
| `risk_tier` | `risk_tier` | `'medium'` |
| `risk_score` | `risk_score` | `0` |
| `risk_dimensions` | `risk_dimensions` (nested object with `structural_risk`, `fidelity_risk`, `evidence_gap_risk`, `statefulness_risk`, `generation_risk`, `ffi_risk`, `semantics_proxy_risk` sub-objects, each containing `score` (int) and `reasons` (string array)) | `{}` |
| `risk_reasons` | `risk_reasons` (string array) | `[]` |
| `acceptability_score` | `acceptability_score` | `0` |
| `acceptability_band` | `acceptability_band` | `'unknown'` |
| `parity_coverage_level` | `parity_coverage_level` | `'unknown'` |
| `parity_test_status` | `parity_test_status` | `'unknown'` |
| `parity_fixture_count` | `parity_fixture_count` | `0` |
| `parity_case_count` | `parity_case_count` | `0` |
| `review_status` | `review_status` | `'missing'` |
| `review_semantic_verdict` | `review_semantic_verdict` | `'unknown'` |
| `review_developer_semantics_verdict` | `review_developer_semantics_verdict` | `'unknown'` |
| `review_limitations` | `review_limitations` (string array) | `[]` |
| `review_required_actions` | `review_required_actions` (string array) | `[]` |
| `trust_readiness` | `trust_readiness` | `'not_ready'` |
| `trust_blockers` | `trust_blockers` (string array) | `[]` |

### Script

```python
#!/usr/bin/env python3
"""Backfill atom_audit_rollups from audit_manifest.json.

Requires: Phase 1 schema + Phase 2A atoms backfill completed.
Run with: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars.
"""
import json
import os

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

MANIFEST_PATH = os.environ.get(
    "AUDIT_MANIFEST_PATH",
    "../ageo-atoms/data/audit_manifest.json",
)

BATCH_SIZE = 50


def build_rollup_row(atom_id: str, entry: dict) -> dict:
    """Map manifest atom entry to atom_audit_rollups row."""
    return {
        "atom_id": atom_id,
        "overall_verdict": entry.get("overall_verdict") or "unknown",
        "structural_status": entry.get("structural_status") or "unknown",
        "runtime_status": entry.get("runtime_status") or "unknown",
        "semantic_status": entry.get("semantic_status") or "unknown",
        "developer_semantics_status": entry.get("developer_semantics_status") or "unknown",
        "risk_tier": entry.get("risk_tier") or "medium",
        "risk_score": entry.get("risk_score", 0),
        "risk_dimensions": entry.get("risk_dimensions") or {},
        "risk_reasons": entry.get("risk_reasons") or [],
        "acceptability_score": entry.get("acceptability_score", 0),
        "acceptability_band": entry.get("acceptability_band") or "unknown",
        "parity_coverage_level": entry.get("parity_coverage_level") or "unknown",
        "parity_test_status": entry.get("parity_test_status") or "unknown",
        "parity_fixture_count": entry.get("parity_fixture_count", 0),
        "parity_case_count": entry.get("parity_case_count", 0),
        "review_status": entry.get("review_status") or "missing",
        "review_semantic_verdict": entry.get("review_semantic_verdict") or "unknown",
        "review_developer_semantics_verdict": entry.get("review_developer_semantics_verdict") or "unknown",
        "review_limitations": entry.get("review_limitations") or [],
        "review_required_actions": entry.get("review_required_actions") or [],
        "trust_readiness": entry.get("trust_readiness") or "not_ready",
        "trust_blockers": entry.get("trust_blockers") or [],
    }


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    atoms_list = manifest["atoms"]
    print(f"Processing {len(atoms_list)} atoms from manifest...")

    # Pre-fetch all atom fqdn -> atom_id mappings
    all_atoms = supabase.table("atoms").select("atom_id, fqdn").execute()
    fqdn_to_id = {row["fqdn"]: row["atom_id"] for row in all_atoms.data}
    print(f"Found {len(fqdn_to_id)} atoms in database")

    rollup_batch = []
    skipped = 0
    total_rollups = 0

    for entry in atoms_list:
        fqdn = entry["atom_name"]
        atom_id = fqdn_to_id.get(fqdn)
        if atom_id is None:
            skipped += 1
            continue

        rollup_batch.append(build_rollup_row(atom_id, entry))

        if len(rollup_batch) >= BATCH_SIZE:
            supabase.table("atom_audit_rollups").upsert(rollup_batch).execute()
            total_rollups += len(rollup_batch)
            rollup_batch = []

    # Flush remaining
    if rollup_batch:
        supabase.table("atom_audit_rollups").upsert(rollup_batch).execute()
        total_rollups += len(rollup_batch)

    print(f"Upserted {total_rollups} rollup rows, skipped {skipped} unresolved atoms")


if __name__ == "__main__":
    main()
```

---

## Step 7: Migrate Existing `fuzz_results` into `atom_audit_evidence`

The existing `fuzz_results` table (from `migrations/004_phase_d_ecosystem.sql`) has
this schema:

```sql
-- Existing schema (read-only after migration)
CREATE TABLE fuzz_results (
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
```

### Migration SQL

Run this after the `atom_audit_evidence` table exists and the `atoms` table has been
backfilled (Phase 2A):

```sql
INSERT INTO public.atom_audit_evidence (
    atom_id,
    audit_type,
    passed,
    details,
    source_kind,
    runner_version,
    created_at
)
SELECT
    a.atom_id,
    'fuzz_test',
    fr.passed,
    jsonb_build_object(
        'strategy', fr.strategy,
        'inputs_tested', fr.inputs_tested,
        'failures', fr.failures,
        'runtime_ms', fr.runtime_ms,
        'content_hash', fr.content_hash,
        'original_fuzz_id', fr.fuzz_id::text
    ),
    'automated',
    'backfill-from-fuzz_results',
    fr.created_at
FROM public.fuzz_results fr
JOIN public.atoms a ON a.fqdn = fr.atom_fqdn;
```

After this migration:
- The `fuzz_results` table becomes **read-only** for backward compatibility with
  Phase D ecosystem queries.
- All new fuzz runs write to `atom_audit_evidence` with `audit_type = 'fuzz_test'`.

---

## Step 8: Backfill Verification Data from Old PG

The three verification tables are populated via direct `pg_dump` / `psql` INSERT
export from the old PostgreSQL database. UUIDs are preserved.

### 8a: Export from old PG

```bash
# Export verification_budgets
pg_dump --data-only --table=verification_budgets \
    --column-inserts --no-owner --no-privileges \
    -f /tmp/verification_budgets.sql \
    "$OLD_PG_URL"

# Export verification_runs
pg_dump --data-only --table=verification_runs \
    --column-inserts --no-owner --no-privileges \
    -f /tmp/verification_runs.sql \
    "$OLD_PG_URL"

# Export bounty_best_scores
pg_dump --data-only --table=bounty_best_scores \
    --column-inserts --no-owner --no-privileges \
    -f /tmp/bounty_best_scores.sql \
    "$OLD_PG_URL"
```

### 8b: Import to Supabase

```bash
# Schema-qualify table names in the dump files if needed, then:
psql "$SUPABASE_DB_URL" -f /tmp/verification_budgets.sql
psql "$SUPABASE_DB_URL" -f /tmp/verification_runs.sql
psql "$SUPABASE_DB_URL" -f /tmp/bounty_best_scores.sql
```

### 8c: Verification field mapping

No transformation is needed -- the schema is carried forward identically. The mapping
is 1:1 from old PG to Supabase:

**`verification_budgets`**

| Column | Old PG type | Supabase type | Notes |
|---|---|---|---|
| `bounty_id` | UUID PK | UUID PK | FK to bounties preserved |
| `tier` | TEXT | TEXT | CHECK constraint carried forward |
| `total_slots` | INT | INT | |
| `used_slots` | INT | INT | Default 0 |
| `cost_per_extra` | NUMERIC(10,2) | NUMERIC(10,2) | |
| `overhead_deposit` | NUMERIC(10,2) | NUMERIC(10,2) | Default 0 |
| `overhead_used` | NUMERIC(10,2) | NUMERIC(10,2) | Default 0 |
| `created_at` | TIMESTAMPTZ | TIMESTAMPTZ | |

**`verification_runs`**

| Column | Old PG type | Supabase type | Notes |
|---|---|---|---|
| `id` | UUID PK | UUID PK | |
| `bounty_id` | UUID FK | UUID FK | FK to bounties |
| `submission_id` | UUID FK | UUID FK | FK to submissions |
| `split_type` | TEXT | TEXT | CHECK: 'public', 'blind' |
| `status` | TEXT | TEXT | CHECK: 'pending', 'running', 'completed', 'failed' |
| `metric_values` | JSONB | JSONB | |
| `output_hash` | TEXT | TEXT | |
| `execution_time_s` | FLOAT | FLOAT | |
| `peak_memory_bytes` | BIGINT | BIGINT | |
| `is_deterministic` | BOOLEAN | BOOLEAN | |
| `sandbox_job_id` | TEXT | TEXT | |
| `slot_consumed` | BOOLEAN | BOOLEAN | Default false |
| `started_at` | TIMESTAMPTZ | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ | TIMESTAMPTZ | |

**`bounty_best_scores`**

| Column | Old PG type | Supabase type | Notes |
|---|---|---|---|
| `bounty_id` | UUID | UUID | Composite PK with metric_name |
| `metric_name` | TEXT | TEXT | Composite PK with bounty_id |
| `best_value` | FLOAT | FLOAT | |
| `best_submission_id` | UUID FK | UUID FK | FK to submissions |
| `is_baseline` | BOOLEAN | BOOLEAN | Default false |
| `updated_at` | TIMESTAMPTZ | TIMESTAMPTZ | |

---

## Step 9: Initial Refresh of `atom_audit_latest`

After all evidence rows have been inserted (Steps 5 and 7), refresh the materialized
view:

```sql
-- First refresh must be non-concurrent (the view has no data yet)
REFRESH MATERIALIZED VIEW public.atom_audit_latest;

-- All subsequent refreshes (after audit runs) should use CONCURRENTLY
-- to avoid locking readers:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY public.atom_audit_latest;
```

---

## Execution Order

Run the steps in this order:

```
Step 1: CREATE TABLE atom_audit_evidence     ─┐
Step 2: CREATE MATERIALIZED VIEW              │
Step 3: CREATE TABLE atom_audit_rollups       │  DDL, no data dependency
Step 4a: CREATE TABLE verification_budgets    │
Step 4b: CREATE TABLE verification_runs       │
Step 4c: CREATE TABLE bounty_best_scores     ─┘
                    │
                    ▼
Step 5: Backfill evidence from audit_manifest.json  ─┐
Step 6: Backfill rollups from audit_manifest.json    │  Can run in parallel
Step 7: Migrate fuzz_results into evidence           │  (different tables / no conflicts)
Step 8: Backfill verification tables from old PG    ─┘
                    │
                    ▼
Step 9: REFRESH MATERIALIZED VIEW atom_audit_latest
```

Steps 5, 6, 7, and 8 can run in parallel because they write to different tables (or
different rows of the same table with no conflicts). Step 8 is independent of all
audit steps since verification tables have no foreign keys to audit tables.

---

## Validation Queries

### Row counts

| Check | Expected |
|-------|----------|
| `atom_audit_rollups` row count | Equal to number of atoms resolved from manifest (up to 505) |
| `atom_audit_evidence` rows from backfill | 3-5 per atom (structural + risk + optional semantic/parity/smoke), roughly 1500-2500 |
| `atom_audit_evidence` rows from fuzz migration | Equal to `SELECT count(*) FROM fuzz_results WHERE atom_fqdn IN (SELECT fqdn FROM atoms)` |
| `atom_audit_latest` row count | Equal to `SELECT count(DISTINCT (atom_id, audit_type)) FROM atom_audit_evidence` |
| `verification_budgets` row count | Equal to old PG `SELECT count(*) FROM verification_budgets` |
| `verification_runs` row count | Equal to old PG `SELECT count(*) FROM verification_runs` |
| `bounty_best_scores` row count | Equal to old PG `SELECT count(*) FROM bounty_best_scores` |

### Audit verification queries

```sql
-- 1. Every rollup row references a valid atom
SELECT count(*) FROM atom_audit_rollups ar
WHERE NOT EXISTS (SELECT 1 FROM atoms a WHERE a.atom_id = ar.atom_id);
-- Expected: 0

-- 2. Every evidence row references a valid atom
SELECT count(*) FROM atom_audit_evidence ae
WHERE NOT EXISTS (SELECT 1 FROM atoms a WHERE a.atom_id = ae.atom_id);
-- Expected: 0

-- 3. Rollup verdicts match manifest distribution
SELECT overall_verdict, count(*)
FROM atom_audit_rollups
GROUP BY overall_verdict
ORDER BY count(*) DESC;
-- Compare against manifest summary

-- 4. Risk tier distribution matches manifest
SELECT risk_tier, count(*)
FROM atom_audit_rollups
GROUP BY risk_tier;
-- Expected: low=279, medium=226 (from manifest summary)

-- 5. Materialized view is populated and has one row per (atom, audit_type)
SELECT audit_type, count(*)
FROM atom_audit_latest
GROUP BY audit_type
ORDER BY audit_type;

-- 6. Fuzz migration preserved all eligible rows
SELECT
    (SELECT count(*) FROM fuzz_results fr
     JOIN atoms a ON a.fqdn = fr.atom_fqdn) AS expected,
    (SELECT count(*) FROM atom_audit_evidence
     WHERE runner_version = 'backfill-from-fuzz_results') AS actual;
-- Expected: expected = actual

-- 7. No orphaned rollups (every rollup atom has at least one evidence row)
SELECT count(*) FROM atom_audit_rollups ar
WHERE NOT EXISTS (
    SELECT 1 FROM atom_audit_evidence ae WHERE ae.atom_id = ar.atom_id
);
-- Expected: 0

-- 8. Structural status counts match manifest
SELECT
    (details->>'status') AS structural_status,
    count(*)
FROM atom_audit_evidence
WHERE audit_type = 'structural_audit'
  AND runner_version = 'backfill-v1'
GROUP BY details->>'status';
-- Expected: pass=424, partial=81 (from manifest summary.structural_status_counts)

-- 9. Runtime status counts for smoke tests match manifest
SELECT
    (details->>'status') AS runtime_status,
    count(*)
FROM atom_audit_evidence
WHERE audit_type = 'smoke_test'
  AND runner_version = 'backfill-v1'
GROUP BY details->>'status';
-- Expected: pass=93, partial=7, fail=2 (from manifest summary.runtime_status_counts,
--   excluding not_applicable=403)

-- 10. Parity coverage level distribution in evidence
SELECT
    (details->>'coverage_level') AS parity_level,
    count(*)
FROM atom_audit_evidence
WHERE audit_type = 'parity_check'
  AND runner_version = 'backfill-v1'
GROUP BY details->>'coverage_level';
-- Expected: not_applicable=98, parity_or_usage_equivalent=68,
--   positive_and_negative=39, positive_path=8
--   (from manifest summary.parity_coverage_level_counts, excluding none=292)
```

### Verification validation queries

```sql
-- 11. Every verification_budget references a valid bounty
SELECT count(*) FROM verification_budgets vb
WHERE NOT EXISTS (SELECT 1 FROM bounties b WHERE b.bounty_id = vb.bounty_id);
-- Expected: 0

-- 12. Every verification_run references a valid bounty and submission
SELECT count(*) FROM verification_runs vr
WHERE NOT EXISTS (SELECT 1 FROM bounties b WHERE b.bounty_id = vr.bounty_id)
   OR NOT EXISTS (SELECT 1 FROM submissions s WHERE s.submission_id = vr.submission_id);
-- Expected: 0

-- 13. Every bounty_best_scores references a valid bounty
SELECT count(*) FROM bounty_best_scores bbs
WHERE NOT EXISTS (SELECT 1 FROM bounties b WHERE b.bounty_id = bbs.bounty_id);
-- Expected: 0

-- 14. Verification budget tier values match bounty tier values
SELECT count(*) FROM verification_budgets vb
JOIN bounties b ON b.bounty_id = vb.bounty_id
WHERE vb.tier != b.tier;
-- Expected: 0 (budgets should match their bounty's tier)

-- 15. used_slots does not exceed total_slots (soft check, warn only)
SELECT bounty_id, used_slots, total_slots
FROM verification_budgets
WHERE used_slots > total_slots;
-- Expected: empty (or flagged for investigation)

-- 16. Row counts match old PG (run against both databases)
-- Old PG:
SELECT 'verification_budgets' AS tbl, count(*) FROM verification_budgets
UNION ALL
SELECT 'verification_runs', count(*) FROM verification_runs
UNION ALL
SELECT 'bounty_best_scores', count(*) FROM bounty_best_scores;
-- Supabase: same query, compare counts
```

---

## Rollback Procedure

### Full rollback (drop all Phase 2B objects)

Rollback drops all objects in reverse dependency order. The `fuzz_results` table is
untouched (it was only read during migration). The old PG verification data is also
untouched.

```sql
-- 1. Drop materialized view (no cascade needed, nothing depends on it)
DROP MATERIALIZED VIEW IF EXISTS public.atom_audit_latest;

-- 2. Drop rollups table
DROP TABLE IF EXISTS public.atom_audit_rollups;

-- 3. Drop evidence table (this also drops indexes and RLS policies)
DROP TABLE IF EXISTS public.atom_audit_evidence;

-- 4. Drop verification tables (reverse order of FK dependencies)
DROP TABLE IF EXISTS public.bounty_best_scores;
DROP TABLE IF EXISTS public.verification_runs;
DROP TABLE IF EXISTS public.verification_budgets;
```

### Data-only rollback (keep schema, remove backfilled data)

If only the backfill data needs to be rolled back while preserving the schema for
re-running the backfill:

```sql
-- Remove backfilled evidence rows
DELETE FROM public.atom_audit_evidence
WHERE runner_version IN ('backfill-v1', 'backfill-from-fuzz_results');

-- Remove all rollup rows (they were all from the backfill)
TRUNCATE public.atom_audit_rollups;

-- Clear verification tables
TRUNCATE public.bounty_best_scores;
TRUNCATE public.verification_runs;
TRUNCATE public.verification_budgets;

-- Refresh the materialized view (will now be empty)
REFRESH MATERIALIZED VIEW public.atom_audit_latest;
```

### Partial rollback (verification tables only)

If only the verification backfill needs to be reverted:

```sql
TRUNCATE public.bounty_best_scores;
TRUNCATE public.verification_runs;
TRUNCATE public.verification_budgets;
```

---

## Notes

- The `audit_manifest.json` field `runtime_status` has 403 atoms with value
  `not_applicable`. These are skipped for smoke_test evidence creation since they
  represent atoms that have not been runtime-tested, not actual audit results.
- The `semantic_status` field has 403 atoms with value `unknown`. These are also
  skipped for semantic_audit evidence to avoid polluting the evidence table with
  non-results.
- The `developer_semantics_status` field is `unknown` for all 505 atoms in the
  current manifest. It is captured in rollups (as `unknown`) but no evidence row is
  created for it.
- The `risk_dimensions` JSONB in rollups preserves the full nested structure from the
  manifest (seven sub-objects: `structural_risk`, `fidelity_risk`,
  `evidence_gap_risk`, `statefulness_risk`, `generation_risk`, `ffi_risk`,
  `semantics_proxy_risk`), each containing `score` and `reasons` fields.
- The fuzz_results migration includes `content_hash` and `original_fuzz_id` in the
  evidence `details` JSONB to maintain traceability back to the original rows.
- Verification tables (`verification_budgets`, `verification_runs`,
  `bounty_best_scores`) are structurally identical to the old PG schema in
  `migrations/003_phase_c_verification.sql`. No column renames, type changes, or
  constraint modifications. The only additions are RLS policies.
- Verification tables are readable by all authenticated users but writable only by the
  service role (the verification pipeline). This matches the audit evidence write
  pattern.
- The `bounty_best_scores` table uses a composite primary key `(bounty_id,
  metric_name)` rather than a surrogate UUID. This is preserved from the original
  schema.
