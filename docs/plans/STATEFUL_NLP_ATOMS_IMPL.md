# Stateful NLP Atoms — Implementation Plan

Concrete implementation steps to close the gap between the current `sciona-infra`
codebase and the architecture described in `STATEFUL_NLP_ATOMS.md`.

**Current state:** The unified artifacts model (`artifacts`, `artifact_versions`,
`artifact_io_specs`, `artifact_parameters`, `artifact_ports`, CDG nodes/edges/bindings,
audit evidence/rollups) supports `artifact_kind IN ('atom', 'cdg')`. The registry API
(`POST /atoms`) only handles code atoms with base64 tar uploads. There is no concept of
state artifacts, asset files, content-addressed caching, format security scanning, or
state port declarations.

**Target state:** Logic atoms and state artifacts are composable primitives in the same
registry. State artifacts are immutable, hash-pinned, format-restricted pure-data
resources. Logic atoms declare typed state ports. The runtime can hydrate assets
offline with cryptographic verification. Audit and LaaS coverage extend to
state artifact provenance and boundary review.

---

## Phase 0: Decisions and Fixtures

**Goal:** Lock down design decisions and create test fixtures before any schema work.

### Decisions to record

1. **Multi-file manifest format:** Use canonical JSON manifest hash (sorted keys,
   no whitespace, SHA-256 of the canonical bytes). Defer Merkle roots.
   ```json
   {
     "files": [
       {"path": "model.onnx", "sha256": "abc123...", "byte_size": 51200},
       {"path": "vocab.json", "sha256": "def456...", "byte_size": 8192}
     ]
   }
   ```
   The version `content_hash` for multi-file state artifacts = SHA-256 of this
   canonical JSON string.

2. **Initial allowed formats:** `safetensors`, `onnx`, `json`, `jsonl`, `parquet`,
   `npy`, `npz`, `txt`, `vocab`.

3. **Runtime resolver location:** `sciona_infra/assets/` (inside infra for now;
   extract to `sciona.assets` package later if CLI runtime separates).

### File changes

| Action | File | Notes |
|--------|------|-------|
| Create | `tests/fixtures/state_artifacts/org_taxonomy_en.json` | Small JSON taxonomy (~20 entries). Record SHA-256 in test constants. |
| Create | `tests/fixtures/state_artifacts/dummy_model.onnx` | Minimal ONNX graph (single Add node). Record SHA-256. |
| Create | `tests/fixtures/state_artifacts/blocked_pickle.pkl` | Tiny pickle file for rejection tests. |
| Create | `tests/fixtures/state_artifacts/renamed_pickle.json` | Pickle bytes with `.json` extension for magic-byte tests. |
| Create | `tests/fixtures/state_artifacts/manifest.json` | Canonical manifest for the multi-file case. |
| Create | `tests/conftest_state_artifacts.py` | Pytest fixtures exposing paths, expected hashes, and helper functions. |

### Acceptance

- All fixture files have stable SHA-256 hashes asserted in a test.
- Canonical manifest hash is deterministic across runs.

---

## Phase 1: Schema and RPCs

**Goal:** Extend the database to support state artifacts, their assets, metadata,
state ports, and dependency pins.

### Migration file

Create `supabase/migrations/20260428000000_stateful_nlp_atoms.sql`:

#### 1a. Extend `artifacts.artifact_kind`

```sql
ALTER TABLE public.artifacts
    DROP CONSTRAINT IF EXISTS artifacts_artifact_kind_check;
ALTER TABLE public.artifacts
    ADD CONSTRAINT artifacts_artifact_kind_check
    CHECK (artifact_kind IN ('atom', 'cdg', 'state_artifact'));
```

For state artifacts: `source_module_path = ''`, `source_symbol = ''`,
`stateful_kind = 'explicit_state_model'`, `is_stochastic = false`, `is_ffi = false`.

#### 1b. Create `artifact_assets`

Per plan spec — `asset_id`, `version_id`, `asset_path`, `byte_size`, `sha256`,
`format` (allowlisted CHECK), `media_type`, `storage_uri`, `compression`,
`mmap_safe`, `loader_name`, `created_at`. Unique on `(version_id, asset_path)`
and `(version_id, sha256)`.

#### 1c. Create `state_artifact_metadata`

Per plan spec — `version_id` (PK, FK to `artifact_versions`), `resource_family`,
`language_tags`, `vocabulary_size`, `embedding_dim`, `max_sequence_length`,
`label_schema`, `training_data_summary`, `provenance_summary`, `intended_use`,
`limitations`, `legal_basis`, `deterministic_output_precision`, `created_at`.

#### 1d. Create `artifact_state_ports`

Per plan spec — `state_port_id`, `artifact_id`, `version_id`, `port_name`,
`type_desc`, `accepted_formats`, `required_metadata`, `required`, `ordinal`.
Unique on `(artifact_id, version_id, port_name)`.

#### 1e. Create `artifact_dependencies`

Per plan spec — `dependency_id`, `dependent_version_id`, `dependency_artifact_fqdn`,
`dependency_content_hash`, `dependency_role` (CHECK `IN ('state_artifact',
'logic_atom', 'cdg')`), `port_name`, `optional`, `binding_metadata`, `created_at`.
Unique on `(dependent_version_id, dependency_artifact_fqdn,
dependency_content_hash, port_name)`.

#### 1f. Extend audit evidence types

Add new `audit_type` values to the existing CHECK constraint on
`artifact_audit_evidence`:

- `asset_integrity_check`
- `format_security_scan`
- `loader_policy_check`
- `provenance_review`
- `license_ip_review`
- `privacy_review`
- `golden_eval`
- `determinism_replay`
- `boundary_review`

#### 1g. Update `artifact_is_publishable()`

Add a state-artifact branch:

```
-- For state_artifact kind:
--   at least one artifact_assets row
--   state_artifact_metadata row exists
--   dejargonized description exists
--   at least one reference
--   audit rollup exists
```

#### 1h. Update `get_artifact_document()`

Add sections to the returned JSONB:

- `'assets'` — from `artifact_assets` joined via version
- `'state_metadata'` — from `state_artifact_metadata` joined via version
- `'state_ports'` — from `artifact_state_ports`
- `'dependencies'` — from `artifact_dependencies` joined via version

#### 1i. Update `catalog_artifacts_served`

Add a third UNION ALL leg for `artifact_kind = 'state_artifact'` with extra
columns: `resource_family`, `language_tags`.

#### 1j. Indexes

- `idx_artifact_assets_version` on `artifact_assets(version_id)`
- `idx_artifact_assets_sha256` on `artifact_assets(sha256)`
- `idx_state_metadata_family` on `state_artifact_metadata(resource_family)`
- `idx_state_ports_artifact` on `artifact_state_ports(artifact_id)`
- `idx_dependencies_dependent` on `artifact_dependencies(dependent_version_id)`
- `idx_dependencies_fqdn` on `artifact_dependencies(dependency_artifact_fqdn)`

### API model changes

| Action | File | Notes |
|--------|------|-------|
| Edit | `sciona_infra/api/models.py` | Add Pydantic models: `AssetEntry`, `StateArtifactMetadata`, `StatePortDeclaration`, `DependencyPin`, `StateArtifactPublishRequest`, `StateArtifactPublishResponse`, `ArtifactDocumentResponse` (extended). |

### Test changes

| Action | File | Notes |
|--------|------|-------|
| Create | `tests/test_stateful_artifacts_schema.py` | Supabase local integration tests: insert state artifact + assets + metadata; verify `artifact_is_publishable` rejects incomplete; verify `get_artifact_document` returns assets/metadata/ports/dependencies. |

### Acceptance

- State artifacts can be inserted, queried, and served through artifact document RPCs.
- A state artifact without asset rows is not publishable.
- `get_artifact_document` returns the new sections for state artifacts.

---

## Phase 2: Publish and Verification Pipeline

**Goal:** API endpoints for publishing state artifacts with hash verification and
format security scanning.

### New router

Create `sciona_infra/api/routers/state_artifacts.py`:

```
POST /artifacts/state
    — Create a new state artifact (fqdn, semver, description, metadata, assets manifest)
    — Returns artifact_id, version_id, presign URLs for asset upload

POST /artifacts/{fqdn}/versions
    — Create a new version of an existing state artifact

POST /artifacts/{fqdn}/assets/presign
    — Generate S3 presigned upload URLs for asset files
    — Client uploads directly to S3

POST /artifacts/{fqdn}/versions/{version_id}/verify-assets
    — Trigger server-side verification:
      1. Download each asset from S3
      2. Recompute SHA-256 from bytes
      3. Compare against declared hashes in artifact_assets
      4. Run format scanner (magic bytes, extension, blocked serializer check)
      5. Write audit evidence rows (asset_integrity_check, format_security_scan)
      6. Update asset rows with verified status
```

### Format scanner

Create `sciona_infra/assets/format_scanner.py`:

```python
ALLOWED_FORMATS = {'safetensors', 'onnx', 'json', 'jsonl', 'parquet',
                   'npy', 'npz', 'txt', 'vocab'}

BLOCKED_MAGIC_BYTES = {
    b'\x80\x05': 'pickle_v5',
    b'\x80\x04': 'pickle_v4',
    b'\x80\x03': 'pickle_v3',
    b'\x80\x02': 'pickle_v2',
}

def scan_asset(path: Path, declared_format: str) -> ScanResult:
    """Check magic bytes, extension, and blocked patterns."""
    ...

def verify_sha256(path: Path, expected: str) -> bool:
    """Stream file through SHA-256, compare to expected hash."""
    ...
```

Rules:
- Pickle magic bytes (`\x80\x02` through `\x80\x05`) in any file = reject
- `.pkl`, `.pickle`, `.joblib` extension = reject regardless of magic bytes
- `torch.load` marker patterns = reject
- For `.npz`: verify NumPy header, confirm no pickle streams
- Extension must match `declared_format`

### Loader policy module

Create `sciona_infra/assets/loader_policy.py`:

Mapping of format -> allowed loader + constraints:

| Format | Loader | Constraint |
|--------|--------|------------|
| `safetensors` | `safetensors.safe_open` | No torch pickle fallback |
| `onnx` | `onnxruntime.InferenceSession` | `disabled_optimizers`, no custom ops by default |
| `json`/`jsonl` | `json.load`/`json.loads` | Schema validation against declared `label_schema` |
| `parquet` | `pyarrow.parquet.read_table` | Explicit schema check |
| `npy`/`npz` | `numpy.load` | `allow_pickle=False`, dtype allowlist |
| `txt`/`vocab` | UTF-8 text read | Normalized line endings |

### File changes

| Action | File | Notes |
|--------|------|-------|
| Create | `sciona_infra/api/routers/state_artifacts.py` | Four endpoints above |
| Create | `sciona_infra/assets/__init__.py` | Package init |
| Create | `sciona_infra/assets/format_scanner.py` | Magic byte + extension + blocked pattern checks |
| Create | `sciona_infra/assets/loader_policy.py` | Format-to-loader mapping and sandbox load test |
| Edit | `sciona_infra/api/app.py` | Mount state_artifacts router at `/artifacts` |
| Edit | `sciona_infra/api/models.py` | Add request/response models for publish + verify |
| Create | `tests/test_state_artifact_publish.py` | Integration tests: wrong hash fails, pickle rejected, valid formats pass |
| Create | `tests/test_format_scanner.py` | Unit tests for magic byte detection, extension spoofing |

### Acceptance

- Uploading bytes with a wrong declared hash fails verification.
- Renamed pickle/joblib/torch artifacts fail format scanner.
- Valid JSON/Parquet/ONNX/Safetensors fixtures produce verified asset rows and
  `asset_integrity_check` + `format_security_scan` audit evidence.

---

## Phase 3: Runtime Hydration

**Goal:** Content-addressed local cache with cryptographic verification, offline
strict mode, and AOT hydration.

### Asset resolver

Create `sciona_infra/assets/resolver.py`:

```python
@dataclass(frozen=True)
class AssetFile:
    asset_path: str
    sha256: str
    byte_size: int
    storage_uri: str
    format: str
    loader_name: str

@dataclass(frozen=True)
class AssetRef:
    fqdn: str
    content_hash: str
    assets: tuple[AssetFile, ...]

class CryptographicIntegrityError(RuntimeError):
    """Raised when downloaded bytes do not match the declared SHA-256 hash."""

def hydrate_asset(ref: AssetRef, *, cache_dir: Path, network: bool = True) -> Path:
    """Download, verify, and cache a single asset. Returns cache path."""
    # 1. Check cache: ~/.sciona/assets/sha256/<hash>/
    # 2. If miss and network=True: download to temp file, stream through SHA-256
    # 3. If hash matches: atomic rename into cache
    # 4. If mismatch: delete temp, raise CryptographicIntegrityError
    # 5. If miss and network=False: raise CryptographicIntegrityError
    ...

def resolve_asset_path(ref: AssetRef, *, cache_dir: Path, network: bool = True) -> Path:
    """Return local path to a verified asset, hydrating if necessary."""
    ...
```

Cache layout:
```
~/.sciona/assets/
├── sha256/
│   ├── abc123.../
│   │   └── model.onnx
│   └── def456.../
│       └── vocab.json
└── manifest.sqlite   # local tracking: fqdn, content_hash, byte_size, verified_at, storage_uri
```

Key behaviors:
- Downloads write to `<cache_dir>/tmp/<uuid>`, stream bytes through `hashlib.sha256`,
  verify, then `os.rename` into `sha256/<hash>/<asset_path>`
- S3 ETag is **never** treated as integrity proof
- Concurrent hydration uses file locks (`fcntl.flock`) on the temp path
- SQLite manifest tracks what's cached for quick inventory

### AOT hydrator

Create `sciona_infra/assets/hydrator.py`:

```python
async def hydrate_cdg(
    fqdn: str,
    *,
    supabase,
    cache_dir: Path,
    strict: bool = False,
) -> HydrationReceipt:
    """
    1. Call get_artifact_document(fqdn)
    2. Traverse artifact_dependencies recursively
    3. For each state_artifact dependency: hydrate all assets
    4. Verify all hashes
    5. Write offline lockfile: ~/.sciona/assets/lockfiles/<cdg-hash>.lock.json
    """
    ...

@dataclass(frozen=True)
class HydrationReceipt:
    cdg_fqdn: str
    resolved_dependencies: tuple[ResolvedDependency, ...]
    lockfile_path: Path
    all_verified: bool
```

Lockfile format (JSON):
```json
{
  "cdg_fqdn": "sciona.cdgs.nlp.org_ner.en.v1",
  "hydrated_at": "2026-04-28T12:00:00Z",
  "dependencies": [
    {
      "fqdn": "sciona.resources.nlp.org_taxonomy.en.v1",
      "content_hash": "abc123...",
      "assets": [
        {"path": "taxonomy.json", "sha256": "def456...", "cached_at": "..."}
      ]
    }
  ]
}
```

### CLI entry point

Add to the CLI or as a script:

```bash
sciona hydrate <cdg-fqdn> --strict --cache-dir ~/.sciona/assets
```

For now, implement as `sciona_infra/assets/cli.py` with a `hydrate` function
callable from scripts or a future CLI.

### File changes

| Action | File | Notes |
|--------|------|-------|
| Create | `sciona_infra/assets/resolver.py` | `AssetRef`, `AssetFile`, `CryptographicIntegrityError`, `hydrate_asset`, `resolve_asset_path` |
| Create | `sciona_infra/assets/hydrator.py` | `hydrate_cdg`, `HydrationReceipt`, lockfile writer |
| Create | `sciona_infra/assets/cli.py` | `hydrate` command entry point |
| Create | `sciona_infra/assets/cache_db.py` | SQLite manifest helpers (insert/query/prune) |
| Create | `tests/test_asset_resolver.py` | Unit tests: cache hit, cache miss + download, hash mismatch, strict mode, concurrent hydration |
| Create | `tests/test_hydrator.py` | Integration test: hydrate CDG with state artifact deps, verify lockfile |

### Acceptance

- Hydration succeeds for valid fixture hashes.
- Tampered bytes (modified after download start) raise `CryptographicIntegrityError`.
- `--strict` mode performs no network access and fails on cache miss.
- Cache hit path never touches network (verified by mocking S3 client).
- Concurrent hydration of the same hash is atomic (no partial files).

---

## Phase 4: Logic Atom State Ports

**Goal:** Logic atoms can declare typed state resource ports. CDGs bind those ports
to exact state artifact versions.

### Contribution contract update

Logic atoms that wrap NLP resources must:
1. Declare state ports via `artifact_state_ports` rows
2. Accept state resources as explicit dependency-injected handles (not global paths)
3. Emit Sciona-standard typed outputs (not raw library tags)
4. Set all random seeds, lock tokenizer options, round floats to declared precision

### Metadata sidecar extension

Extend the atom contribution metadata (in `sciona-atoms` repo or publish request)
to include:

```python
# In the atom's register() or metadata sidecar:
state_ports = [
    StatePortDeclaration(
        port_name="taxonomy",
        type_desc="JSON taxonomy mapping entity labels to Sciona standard types",
        accepted_formats=["json"],
        required_metadata={"label_schema": {"kind": "entity_taxonomy"}},
        required=True,
    ),
]
```

### Example wrapper

Create a reference implementation in `sciona-atoms` (or as a test fixture in
`sciona-infra`):

`sciona.atoms.nlp.taxonomy_entity_match`:
- Accepts a hydrated JSON taxonomy path via state port
- Loads taxonomy with `json.load` (no pickle)
- Performs exact string match against entity labels
- Returns Sciona-standard output:
  ```json
  {"entity_type": "Organization", "confidence": 1.0, "span_start": 12, "span_end": 27}
  ```
- No hardcoded URL or global resource path
- Deterministic: no randomness, confidence = 1.0 for exact match

### File changes

| Action | File | Notes |
|--------|------|-------|
| Edit | `sciona_infra/api/models.py` | Add `StatePortDeclaration` to atom publish request model |
| Edit | `sciona_infra/api/routers/registry.py` | On atom publish, insert `artifact_state_ports` rows if state ports declared |
| Create | `tests/fixtures/state_artifacts/taxonomy_entity_match.py` | Reference logic atom wrapper |
| Create | `tests/test_state_port_binding.py` | Test: CDG binds logic atom port to exact state artifact hash; golden outputs stable after rounding |

### Acceptance

- Example wrapper has no hardcoded URL or global resource path.
- CDG binding pins the exact state artifact content hash in `artifact_dependencies`.
- Golden outputs are stable after declared rounding across repeated runs.
- Publishing an atom with state ports creates `artifact_state_ports` rows.

---

## Phase 5: Audit and LaaS Policy

**Goal:** Audit templates, tier gate enforcement, and execution receipt fields for
state artifacts.

### Audit templates

Create `sciona_infra/audit/state_artifact_templates.py` defining structured
audit checklists for each new evidence type:

- **`provenance_review`**: Training data origin, pipeline description, data
  license compatibility.
- **`license_ip_review`**: No un-anonymized HIPAA/PII, no unlicensed copyrighted
  material, FRE shield applicability.
- **`privacy_review`**: PII scan results, anonymization method, data retention policy.
- **`golden_eval`**: Hidden test suite results, expected vs actual, determinism check.
- **`boundary_review`**: Declared limitations (e.g., max sequence length), accuracy
  degradation curves, out-of-bound behavior.
- **`determinism_replay`**: Cross-run comparison, rounding verification, hardware
  independence check.

### Tier gate logic

#### Tier 2 (Verified) — automated checks

Edit `artifact_is_publishable()` or create a new
`state_artifact_tier2_gate(version_id)` RPC:

1. All `artifact_assets` rows have matching SHA-256 (via `asset_integrity_check` evidence).
2. All formats are allowlisted and magic bytes match (via `format_security_scan` evidence).
3. No blocked serializers detected (via `loader_policy_check` evidence).
4. Loader can materialize the resource in a sandbox without network access.
5. Logic atom can run a visible golden suite with deterministic outputs.
6. Cross-run replay on the same hardware is byte-identical after declared rounding.

#### Tier 1 (Certified/Insurable) — manual expert review

Add rollup logic that blocks Tier 1 if:
- Any `artifact_dependencies` row has `dependency_content_hash` empty or unpinned.
- Any required `provenance_review`, `license_ip_review`, `privacy_review`,
  `golden_eval`, or `boundary_review` evidence is missing or failed.

### Execution receipt extension

Add fields to the CDG execution receipt (wherever receipts are currently recorded):

```json
{
  "logic_atom_hashes": {"sciona.atoms.nlp.taxonomy_entity_match": "abc123..."},
  "state_artifact_hashes": {"sciona.resources.nlp.org_taxonomy.en.v1": "def456..."},
  "hydration_mode": "aot_strict",
  "boundary_metadata": {"max_sequence_length": 512}
}
```

### Policy enforcement

LaaS coverage applies only when:
- Receipt shows exact certified logic atom content hash
- Receipt shows exact certified state artifact content hash
- Inputs fall within certified boundaries (as declared in `state_artifact_metadata.limitations`)

Execution outside declared limits remains usable but is explicitly not covered.

### File changes

| Action | File | Notes |
|--------|------|-------|
| Create | `sciona_infra/audit/__init__.py` | Package init |
| Create | `sciona_infra/audit/state_artifact_templates.py` | Structured audit checklists |
| Create | `sciona_infra/audit/tier_gates.py` | `tier2_automated_gate()`, `tier1_readiness_check()` |
| Edit | `supabase/migrations/20260428000000_stateful_nlp_atoms.sql` | Add `state_artifact_tier2_gate(version_id)` RPC |
| Create | `tests/test_tier_gates.py` | Test: unpinned deps block Tier 1; missing evidence blocks Tier 2; valid artifact passes |

### Acceptance

- A CDG with unpinned or mutable state dependencies cannot reach Tier 1.
- A certified CDG receipt records logic atom hashes, state artifact hashes, and
  boundary metadata.
- Missing audit evidence for any required type blocks the corresponding tier.

---

## First Vertical Slice (spans Phases 0–5)

Implement end-to-end before broadening:

1. **Publish** `sciona.resources.nlp.org_taxonomy.en.v1` as a JSON state artifact
   using the Phase 2 endpoint.
2. **Publish** `sciona.atoms.nlp.taxonomy_entity_match` as a logic atom with a
   `taxonomy` state port using the extended atom publish flow.
3. **Bind** them in a small CDG with the exact taxonomy content hash in
   `artifact_dependencies`.
4. **Hydrate** the taxonomy into `~/.sciona/assets` using the Phase 3 resolver.
5. **Run** the golden suite and emit a receipt containing both the logic atom hash
   and taxonomy hash.

This validates: registry split, hash pinning, no-pickle policy, local cache,
AOT hydration, typed NLP outputs, and audit evidence flow.

### Vertical slice test

Create `tests/test_vertical_slice_stateful_nlp.py`:

```python
@pytest.mark.integration
async def test_end_to_end_stateful_nlp():
    # 1. Publish state artifact (taxonomy JSON)
    # 2. Verify assets (hash check + format scan)
    # 3. Publish logic atom with state port
    # 4. Create CDG binding with pinned hash
    # 5. Hydrate to local cache
    # 6. Execute logic atom with hydrated resource
    # 7. Assert golden output matches expected
    # 8. Assert receipt contains both hashes
    # 9. Assert Tier 2 gate passes
    # 10. Assert Tier 1 gate blocks (no expert review yet)
```

---

## Sequencing and Dependencies

```
Phase 0 (fixtures)
  └─► Phase 1 (schema + RPCs)
        ├─► Phase 2 (publish + verification)
        │     └─► Phase 3 (runtime hydration)
        └─► Phase 4 (state ports)
              └─► Phase 5 (audit + LaaS)

Vertical slice: runs after Phase 3 + Phase 4 are complete.
```

Phases 2 and 4 can proceed in parallel once Phase 1 is done.
Phase 3 depends on Phase 2 (needs verified asset rows to download from).
Phase 5 depends on Phase 4 (needs state ports for tier gate logic).

## Open Questions (carried from plan doc)

1. **Multi-file vs multi-artifact for large model families:** Start with multi-file
   `artifact_assets` per version. If a model family exceeds ~10 files or ~5GB total,
   revisit with a parent CDG-like resource.

2. **ONNX custom ops:** Ban for Tier 1 initially. Allow only when the custom op
   library is itself a separately audited logic artifact (Phase 5+).

3. **Resolver package location:** Start in `sciona_infra/assets/`. Extract to
   standalone `sciona-assets` package when CLI runtime separates.

4. **Registry attestation signatures:** Defer. SHA-256 pinning is sufficient for
   launch. Add Ed25519 signatures over content hashes as a follow-up if enterprise
   audits require it.
