# Phase D: Ecosystem (Community Flywheel) -- Implementation Plan

### Overview

Phase D builds on Phase A (atom similarity/fingerprinting, provenance schema), Phase B (Registry API, PostgreSQL, S3, auth), and Phase C (settlement pipeline). It adds the community-facing features that create the incentive flywheel: automated fuzzing of public atoms, benchmark-driven ranking, soft deprecation, metrics dashboards, and discipline repo synchronization.

---

### 1. Fuzzing Cluster Architecture

**Goal:** Serverless compute pool that runs on every atom publish, performing property-based testing, parameter smoothing, and behavioral equivalence testing.

#### 1.1 Job Queue and Trigger

- **Trigger:** An SNS notification fires on every atom publish (the same event described in TECH_GAP 3.4 for SQLite snapshot generation). A new SNS subscriber dispatches fuzz jobs.
- **Queue:** SQS FIFO queue (`fuzz-jobs.fifo`) with message deduplication by `atom_fqdn:content_hash`. This prevents duplicate fuzzing when the same atom version is re-published.
- **Asymmetry enforcement:** The Lambda dispatcher checks `atoms.visibility = 'public'` in PostgreSQL before enqueuing. Private/`sources.yml`-only atoms are rejected at this gate.
- **Message schema:**
  ```
  FuzzJobMessage:
    atom_fqdn:      str
    content_hash:    str
    iospec:          list[IOSpec]   # from atom metadata
    tunable_params:  list[PrimitiveParamSpec]  # if any
    benchmark_ids:   list[str]     # benchmark suites applicable to this atom's domain_tags
  ```

#### 1.2 Fuzz Strategy Lambdas

Three Lambda functions, all triggered by SQS:

**a) `fuzz-property-based` -- Hypothesis-style property testing**
- Generates typed inputs from `IOSpec` using a type-to-strategy mapping (e.g., `type_desc="np.ndarray"` maps to `hypothesis.extra.numpy.arrays()`).
- Runs the atom in an isolated subprocess (same approach as `ExecutionSandbox` in `/Users/conrad/personal/sciona/sciona/principal/evaluator.py`).
- Tests: no exceptions, output type matches `IOSpec.type_desc`, no NaN/Inf in numeric outputs, idempotency where declared.
- Runtime: Lambda, 5-minute timeout, 2 GB memory.

**b) `fuzz-boundary-value` -- Boundary value analysis**
- Generates edge-case inputs: empty arrays, single-element, max-size, zero values, negative values, extreme floats.
- Validates graceful handling (no segfaults, meaningful error messages or correct outputs).
- Shares the isolation model from (a).

**c) `fuzz-param-smoothing` -- HPO on tunable_params**
- Reuses the existing `OptunaManager` from `/Users/conrad/personal/sciona/sciona/principal/hpo.py` directly. The `_FallbackStudy` / `_FallbackTrial` classes work without an Optuna dependency, which is suitable for Lambda.
- Runs a short Optuna study (20 trials) over the atom's `tunable_params` against each applicable benchmark dataset.
- Stores the best parameter configuration back to PostgreSQL as `optimized_defaults` on the atom version.
- Runtime: Lambda, 15-minute timeout (standard max), 4 GB memory.

**d) `fuzz-behavioral-equivalence` -- Type-4 plagiarism detection**
- Triggered in batch (not per-publish) as a scheduled job (daily or weekly).
- For each pair of atoms with the same `IOSpec` signature, runs 1000 random typed inputs through both atoms.
- If outputs match on 95%+ of inputs (within floating-point tolerance), flags the pair as `behavioral_equivalent` in a PostgreSQL table for human review.
- This extends the plagiarism detection from `atom_similarity.py`'s fingerprint (Type-1/2) and call-graph overlap (partial Type-3) to cover Type-4 semantic clones.
- Runtime: Lambda or Step Functions for large atom sets.

#### 1.3 Result Storage

Fuzz results go to PostgreSQL:

```sql
CREATE TABLE fuzz_results (
    fuzz_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_fqdn       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    strategy        TEXT NOT NULL,  -- 'property_based', 'boundary_value', 'param_smoothing', 'behavioral_equiv'
    passed          BOOLEAN NOT NULL,
    failures        JSONB DEFAULT '[]',
    inputs_tested   INTEGER NOT NULL,
    runtime_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    FOREIGN KEY (atom_fqdn, content_hash) REFERENCES atom_versions(fqdn, content_hash)
);

CREATE TABLE behavioral_equivalence_flags (
    flag_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_a_fqdn     TEXT NOT NULL,
    atom_a_hash     TEXT NOT NULL,
    atom_b_fqdn     TEXT NOT NULL,
    atom_b_hash     TEXT NOT NULL,
    match_ratio     FLOAT NOT NULL,  -- e.g. 0.97
    sample_size     INTEGER NOT NULL,
    reviewed        BOOLEAN DEFAULT FALSE,
    reviewer_id     UUID,
    disposition     TEXT,  -- 'plagiarism', 'coincidence', 'common_algorithm'
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

---

### 2. Benchmark Schema Additions

#### 2.1 PostgreSQL Schema (Source of Truth)

```sql
-- Benchmark suites
CREATE TABLE benchmark_suites (
    benchmark_id    TEXT PRIMARY KEY,   -- e.g. 'signal_denoising_v2'
    domain_tags     TEXT[] NOT NULL,
    description     TEXT,
    dataset_s3_key  TEXT NOT NULL,      -- pointer to S3 datasets/ prefix
    metric_names    TEXT[] NOT NULL,    -- e.g. ['loss', 'latency_ms', 'memory_mb']
    curation_source TEXT NOT NULL,      -- 'foundation', 'community', 'bounty_derived'
    proposer_id     UUID,              -- NULL for foundation-curated
    vote_count      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'active',  -- 'active', 'retired'
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Per-atom benchmark results (populated by Fuzzing Cluster)
CREATE TABLE atom_benchmarks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_fqdn       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    benchmark_id    TEXT NOT NULL REFERENCES benchmark_suites(benchmark_id),
    metric_name     TEXT NOT NULL,
    metric_value    DOUBLE PRECISION NOT NULL,
    dataset_tag     TEXT NOT NULL,
    measured_at     TIMESTAMPTZ DEFAULT now(),
    fuzz_id         UUID REFERENCES fuzz_results(fuzz_id),
    UNIQUE (atom_fqdn, content_hash, benchmark_id, metric_name)
);

CREATE INDEX idx_atom_benchmarks_lookup 
    ON atom_benchmarks(atom_fqdn, benchmark_id, metric_name);
```

#### 2.2 manifest.sqlite Extension (Local CLI)

The existing `load_hyperparams_manifest_sqlite()` in `/Users/conrad/personal/sciona/sciona/architect/hyperparams.py` queries `atoms` and `hyperparams` tables. Add a new `benchmarks` table to the SQLite snapshot:

```sql
CREATE TABLE benchmarks (
    atom_fqdn       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    benchmark_id    TEXT NOT NULL,
    metric_name     TEXT NOT NULL,
    metric_value    REAL NOT NULL,
    dataset_tag     TEXT NOT NULL,
    measured_at     TEXT NOT NULL,
    PRIMARY KEY (atom_fqdn, content_hash, benchmark_id, metric_name)
);
```

Add a loader function in `hyperparams.py` (or a new `benchmarks.py` module alongside it):

```python
def load_benchmarks_sqlite(db_path: Path) -> dict[str, list[BenchmarkRecord]]:
    """Load per-atom benchmark records from manifest.sqlite."""
```

The `BenchmarkRecord` dataclass mirrors the TECH_GAP 4.12 schema:

```python
@dataclass(frozen=True)
class BenchmarkRecord:
    atom_fqdn: str
    content_hash: str
    benchmark_id: str
    metric_name: str
    metric_value: float
    dataset_tag: str
    measured_at: str
```

---

### 3. UCB1 Prior Integration in AtomLedger

**File:** `/Users/conrad/personal/sciona/sciona/principal/atom_ledger.py`

The current `rank_candidates()` method (line 62) gives untried atoms `float("inf")` UCB score. With benchmark priors, untried atoms with strong benchmark scores should rank higher than untried atoms without benchmarks, but both should still rank above tried atoms (preserving the exploration-first principle).

**Design:**

Add a `benchmark_priors` parameter to the `AtomLedger` constructor or to `rank_candidates()`:

```python
def rank_candidates(
    self,
    slot: SlotSignature,
    candidate_names: list[str],
    exploration_weight: float = 1.414,
    benchmark_priors: dict[str, float] | None = None,  # atom_name -> prior_reward
) -> list[tuple[str, float]]:
```

For untried atoms:
- Without benchmark prior: score = `float("inf")` (unchanged, ensures exploration).
- With benchmark prior: score = `float("inf") - (1.0 - prior_reward)`. This creates a total ordering among untried atoms based on benchmark strength while keeping them all above tried atoms.

For tried atoms:
- The benchmark prior is mixed in as a Bayesian prior on the mean reward. Specifically, treat the benchmark as `k` virtual observations with the benchmark reward, where `k` is a configurable "prior strength" (default 2). This means:
  ```python
  effective_mean = (k * prior_reward + n_plays * empirical_mean) / (k + n_plays)
  ```
  The prior washes out after enough real observations.

**Prior computation from benchmarks:**

A helper function computes the prior for a (slot, atom) pair:

1. Look up benchmarks for the atom matching the slot's `concept_type` and `domain_tags`.
2. Normalize `metric_value` into [0, 1] reward space (lower loss = higher reward).
3. Average across relevant benchmarks.

This helper lives in a new module `sciona/principal/benchmark_priors.py` that reads from the manifest.sqlite benchmarks table.

**Integration point:** In `/Users/conrad/personal/sciona/sciona/commands/optimize_cmds.py` around line 479 where `atom_ledger = AtomLedger()` is created, load benchmark priors from the manifest and pass them when constructing the `PrincipalDeps`.

---

### 4. Atom Soft Deprecation

**Scope:** Add `status` field to atom metadata in both PostgreSQL and manifest.sqlite.

#### 4.1 Status Values

Extend the existing `atoms.status` column (currently has `'approved'`) with `'superseded'`:

```sql
ALTER TABLE atoms ADD COLUMN superseded_by TEXT;  -- fqdn of the superseding atom version
```

#### 4.2 Supersession Detection

A scheduled job (Lambda, runs after fuzz-param-smoothing completes) compares benchmark results:

1. For each atom with `DERIVES_FROM` lineage, compare the newer version's benchmarks against the older version's.
2. If the newer version is strictly better on ALL benchmarks (with a 5% margin), mark the older as `superseded`.
3. Store the `superseded_by` pointer.

#### 4.3 Integration in `sciona optimize`

In `rank_candidates()` (atom_ledger.py), superseded atoms get a penalty factor on their UCB score but are never excluded:

```python
if atom_status.get(name) == 'superseded':
    ucb *= 0.5  # deprioritize but don't exclude
```

The `PrimitiveCatalog` already loads atom metadata; extend it to carry the `status` field through to the ledger.

---

### 5. Metrics and ESG Dashboard

#### 5.1 Data Model (PostgreSQL Views)

**Algorithmic Impact Factor:**
```sql
CREATE VIEW originator_impact AS
SELECT 
    o.originator_id,
    o.affiliation,
    COUNT(DISTINCT b.bounty_id) AS bounty_count,
    SUM(b.escrow_amount) AS total_bounty_value,
    -- h-index analog: largest k such that k bounties each worth >= k
    -- (computed in application layer)
FROM originators o
JOIN authored_by ab ON o.originator_id = ab.originator_id
JOIN depends_on d ON ab.atom_fqdn = d.atom_fqdn
JOIN solved_by sb ON d.cdg_id = sb.cdg_id
JOIN bounties b ON sb.bounty_id = b.bounty_id
WHERE b.status = 'settled'
GROUP BY o.originator_id, o.affiliation;
```

**Cross-Disciplinary Multiplier:**
```sql
CREATE VIEW cross_discipline_usage AS
SELECT 
    a.atom_fqdn,
    a.domain_tags AS original_domain,
    b.domain_tags AS bounty_domain,
    COUNT(*) AS cross_uses
FROM atoms a
JOIN depends_on d ON a.fqdn = d.atom_fqdn
JOIN solved_by sb ON d.cdg_id = sb.cdg_id
JOIN bounties b ON sb.bounty_id = b.bounty_id
WHERE NOT (a.domain_tags && b.domain_tags)  -- no overlap
  AND b.status = 'settled'
GROUP BY a.atom_fqdn, a.domain_tags, b.domain_tags;
```

**Compute Preserved:**
```sql
CREATE VIEW compute_preserved AS
SELECT 
    b.bounty_id,
    b.escrow_amount,
    -- Estimate tokens saved: node_count * avg_tokens_per_agentic_step * avg_agentic_attempts
    (sb.cdg_node_count * 2000 * 5) AS estimated_tokens_saved,
    (sb.cdg_node_count * 2000 * 5 * 0.003) AS estimated_cost_saved  -- at $3/M tokens
FROM bounties b
JOIN solved_by sb ON b.bounty_id = sb.bounty_id
WHERE b.status = 'settled';
```

#### 5.2 API Endpoints (FastAPI on Fargate)

New router module `api/dashboard.py`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard/originator/{id}/impact` | GET | Algorithmic Impact Factor for an originator |
| `/dashboard/originator/{id}/atoms` | GET | All atoms by originator with benchmark scores |
| `/dashboard/atom/{fqdn}/benchmarks` | GET | All benchmark results for an atom |
| `/dashboard/atom/{fqdn}/bibtex` | GET | Auto-generated BibTeX entry |
| `/dashboard/compute-preserved` | GET | Aggregate compute-preserved metrics |
| `/dashboard/leaderboard` | GET | Top originators by impact factor |
| `/dashboard/cross-discipline` | GET | Cross-disciplinary usage statistics |

#### 5.3 Auto-BibTeX

Generate from `AUTHORED_BY` edges in Memgraph:

```python
def generate_bibtex(atom_fqdn: str, graph_store) -> str:
    authors = graph_store.query(
        "MATCH (a:Atom {fqdn: $fqdn})-[:AUTHORED_BY]->(o:Originator) RETURN o",
        {"fqdn": atom_fqdn}
    )
    # Format as @misc{atom_fqdn, author={...}, title={...}, year={...}, 
    #   howpublished={Algorithmic Commons Registry}, note={fqdn:...}}
```

#### 5.4 Frontend Sketch

Static SPA (React or Svelte) served from S3 + CloudFront:
- **Originator Profile:** impact factor, atom list with benchmarks, cross-discipline badges, total compute preserved.
- **Atom Detail:** benchmark history chart (time series of metric values across versions), usage in bounties, BibTeX copy button.
- **Global Leaderboard:** ranked by impact factor, filterable by domain.
- **ESG Summary:** aggregate compute preserved, number of bounties settled, total originator payouts.

---

### 6. Discipline Repo Webhook Sync

#### 6.1 Canonical Format

Discipline repos (e.g., `ageo-atoms-crystallography`) follow the same `manifest.sqlite` schema. The webhook sync Lambda reads from this schema.

#### 6.2 Lambda Design

**Trigger:** GitHub webhook (`push` event) on the discipline repo's `main` branch.

**Processing:**

1. Validate webhook signature (HMAC-SHA256 with shared secret).
2. Clone/fetch the repo at the pushed commit (shallow clone, depth 1).
3. Open the repo's `manifest.sqlite`.
4. For each atom in the repo's `atoms` table:
   a. Check if it exists in global PostgreSQL by `fqdn`.
   b. If new: insert into `atoms`, `atom_versions`, copy source to S3.
   c. If updated (different `content_hash`): insert new version, create `DERIVES_FROM` edge in Memgraph.
   d. Run fingerprint check: compute `fingerprint_function()` from source, check against global fingerprint index for plagiarism.
5. Trigger SNS notification for each new/updated atom (cascading to fuzzing cluster).
6. Regenerate global `manifest.sqlite` snapshot and upload to S3.

**Runtime:** Lambda, 5-minute timeout, 1 GB memory. For large discipline repos, use Step Functions to paginate.

**Idempotency:** The Lambda tracks `last_synced_commit` per discipline repo in PostgreSQL. Re-delivery of the same webhook is a no-op.

#### 6.3 Registration

Discipline repos register via the Registry API:

```
POST /registry/discipline-repos
{
  "repo_url": "https://github.com/org/ageo-atoms-crystallography",
  "webhook_secret": "...",
  "domain_tags": ["crystallography"],
  "maintainer_ids": ["..."]
}
```

---

### 7. Benchmark Suite Curation

#### 7.1 Three-Source Process

**Foundation-curated:** Inserted directly into `benchmark_suites` by admins. Initial set covers core domains already represented by built-in atoms: signal processing, sorting/searching, linear algebra.

**Community-proposed:** API endpoint:
```
POST /benchmarks/propose
{
  "benchmark_id": "ecg_r_peak_detection_v1",
  "domain_tags": ["biomedical", "signal-processing"],
  "description": "...",
  "dataset_s3_key": "datasets/benchmarks/ecg_rpeak_v1.tar.gz",
  "metric_names": ["rmse", "f1_score"]
}
```
Requires minimum reputation threshold (Algorithmic Impact Factor >= 3). Other experts vote. Accepted at 5 votes from domain experts (AIF >= 5 in matching domain).

**Bounty-derived:** After settlement, if the Principal consents (`allow_benchmark_derivation: true` in `config.yml`), the blind test data is anonymized and packaged as a new benchmark. A `DERIVED_FROM_BOUNTY` edge links it to the source bounty.

---

### 8. Test Strategy

#### 8.1 Unit Tests

| Test | File | What it validates |
|------|------|------------------|
| Benchmark prior integration | `tests/test_atom_ledger.py` | UCB1 with priors ranks benchmark-backed atoms higher among untried |
| Benchmark SQLite loader | `tests/test_hyperparams.py` (extend) | Reads `benchmarks` table from test fixture SQLite |
| BibTeX generation | `tests/test_dashboard.py` (new) | Correct BibTeX format from mock graph data |
| Supersession detection | `tests/test_soft_deprecation.py` (new) | Correctly marks atoms as superseded when strictly outperformed |
| Fuzz input generation | `tests/test_fuzzing.py` (new) | IOSpec-to-hypothesis-strategy mapping produces valid typed inputs |
| Behavioral equivalence | `tests/test_atom_similarity.py` (extend) | Same-output detection across atom pairs |
| Webhook sync | `tests/test_webhook_sync.py` (new) | Manifest parsing, dedup, fingerprint check |

#### 8.2 Integration Tests

- End-to-end: publish atom -> fuzz job enqueued -> fuzz results written -> benchmark populated -> manifest.sqlite updated -> `sciona optimize` reads benchmark priors.
- Discipline repo webhook: mock GitHub webhook -> Lambda processes -> atom appears in global registry.

#### 8.3 Performance Tests

- Behavioral equivalence on 500 atom pairs (30-second budget).
- UCB1 with priors on 200 candidates (must stay under 10ms).
- Dashboard queries against 10K bounties (under 500ms).

---

### 9. Risks and Open Questions

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Fuzz compute costs exceed 5% scrape** | Medium | Set per-atom fuzz budget; skip param smoothing for atoms with no tunables; batch behavioral equiv weekly |
| **Behavioral equiv false positives** | High | Common algorithms (sorting, FFT) will trivially match. Maintain an allowlist of "fundamental algorithms" exempt from plagiarism flagging. Add `common_algorithm` disposition. |
| **Benchmark gaming** | Medium | Community-proposed benchmarks require expert votes. Foundation maintains veto power. Anomaly detection on benchmark results (sudden jumps). |
| **Supersession cascades** | Low | Atom A supersedes B, then C supersedes A. Only the direct predecessor is marked; no chain cascading. |
| **manifest.sqlite size growth** | Medium | Benchmarks table adds O(atoms * benchmarks * metrics) rows. At 1000 atoms * 10 benchmarks * 3 metrics = 30K rows, still well within SQLite's comfort zone. Monitor and add pagination if needed. |
| **Prior strength (k) tuning** | Medium | Default k=2 is conservative. Expose as config (`SCIONA_BENCHMARK_PRIOR_STRENGTH`). Run ablation study on existing optimization runs. |

**Open questions:**

1. **Benchmark metric normalization:** Different metrics (loss, latency, memory) have different scales. The prior computation needs a normalization strategy. Proposal: per-benchmark percentile rank (0-1) rather than raw values.
2. **Fuzz coverage threshold:** What minimum input coverage constitutes "adequately fuzzed"? Proposal: 1000 inputs for property-based, 50 for boundary value, configurable.
3. **Discipline repo trust model:** Should discipline repos get auto-merged or require Foundation review? Proposal: auto-merge for registered repos, but all atoms still go through fingerprint/plagiarism checks.

---

### Implementation Sequencing

1. **Week 1-2:** Benchmark schema (PostgreSQL + manifest.sqlite) + loader + `BenchmarkRecord` dataclass. UCB1 prior integration in `atom_ledger.py`. Tests.
2. **Week 3-4:** Fuzzing cluster Lambdas (property-based + boundary value). SQS queue + SNS trigger wiring. Fuzz result storage.
3. **Week 5:** Param smoothing Lambda (reusing `OptunaManager`). Behavioral equivalence batch job.
4. **Week 6:** Soft deprecation logic (supersession detection + `sciona optimize` integration).
5. **Week 7-8:** Dashboard API endpoints + PostgreSQL views. Auto-BibTeX.
6. **Week 9:** Discipline repo webhook sync Lambda. Registration endpoint.
7. **Week 10:** Benchmark suite curation (community proposal/voting endpoints). Bounty-derived benchmark pipeline.
8. **Week 11-12:** Frontend dashboard. Integration tests. Performance testing. Documentation.

---

### Critical Files for Implementation

- `/Users/conrad/personal/sciona/sciona/principal/atom_ledger.py` - Core file to modify: add benchmark prior support to `rank_candidates()` UCB1 scoring
- `/Users/conrad/personal/sciona/sciona/architect/hyperparams.py` - Extend with `load_benchmarks_sqlite()` for reading benchmark records from manifest.sqlite; pattern to follow for the benchmark loader
- `/Users/conrad/personal/sciona/sciona/principal/hpo.py` - Reused by the fuzz-param-smoothing Lambda; `OptunaManager` and `_FallbackStudy` are the core classes to wrap
- `/Users/conrad/personal/sciona/sciona/architect/atom_similarity.py` - Extend `find_overlapping_atoms()` for behavioral equivalence detection (Type-4); `fingerprint_function()` used by webhook sync
- `/Users/conrad/personal/sciona/sciona/commands/optimize_cmds.py` - Integration point where `AtomLedger` and `PrincipalDeps` are wired up (line 479); must pass benchmark priors through to the optimization loop
