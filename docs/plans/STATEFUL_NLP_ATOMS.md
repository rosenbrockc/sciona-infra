# Stateful NLP Atoms Implementation Plan

## Goal

Integrate serialized NLP resources into Sciona without weakening deterministic
replay, auditability, or LaaS underwriting.

The target model treats executable wrappers and serialized resources as separate
registry primitives:

- **Logic atoms:** pure code wrappers with contracts, witnesses, input/output
  schemas, and de-jargonized descriptions. They contain no weights,
  dictionaries, taxonomies, or model bytes.
- **State artifacts:** immutable pure-data resources such as ONNX models,
  Safetensors tensors, JSON dictionaries, Parquet tables, tokenizer vocabularies,
  embedding matrices, and taxonomies. They contain no execution logic.

This should extend the existing unified `artifacts` model in `sciona-infra`
rather than create a parallel registry. The current `artifact_ports` table is
for alternative implementations of logic artifacts; state resources need their
own asset and dependency model.

## Current Fit

Relevant existing pieces:

- `supabase/migrations/20260414000000_unified_artifacts_phase1.sql` already
  introduces `public.artifacts`, `public.artifact_versions`, audit evidence,
  rollups, CDG nodes, and CDG bindings.
- `supabase/migrations/20260423000000_artifact_ports.sql` models optimized
  ports as separate artifacts, which confirms the existing direction of
  composable artifact primitives.
- `sciona_infra/api/routers/registry.py` still has a minimal atom-only publish
  path, so state artifact publishing should be added through an artifact-level
  endpoint rather than patched into the legacy atom route.
- The atom contribution contract already requires deterministic probes,
  references, de-jargonized docs, and explicit interfaces. Stateful resources
  should add stricter gates, not weaken these requirements.

## Design Principles

1. **No mutable locator may define behavior.** S3 keys and URLs are transport
   hints only. Behavior is pinned by SHA-256 content hashes and version rows.
2. **The registry version is the insurable unit.** A CDG, logic atom, and state
   artifact are only Tier 1 eligible when every dependency is resolved to an
   immutable version hash.
3. **Lazy download is an optimization, not a trust boundary.** Runtime fetches
   must verify hashes before use. Tier 1 enterprise execution should support
   strict offline execution after AOT hydration.
4. **Serialized resources are data, never code.** Pickle, `torch.load`,
   `joblib`, and equivalent arbitrary-code loaders are blocked from verified
   tiers.
5. **Audit shifts from code review to provenance, format, boundary, and replay
   review for state artifacts.** Experts do not inspect every float; they
   certify origin, rights, allowed formats, deterministic behavior, and limits.

## Target Data Model

### 1. Extend `artifacts.artifact_kind`

Add `state_artifact`:

```sql
CHECK (artifact_kind IN ('atom', 'cdg', 'state_artifact'))
```

State artifacts use the existing `fqdn`, `owner_id`, `status`,
`visibility_tier`, `description`, and audit fields. For state artifacts:

- `source_module_path` and `source_symbol` remain empty.
- `stateful_kind` should be `explicit_state_model`.
- `is_stochastic` must be `false`.
- `is_ffi` must be `false`.

### 2. Add `artifact_assets`

One state artifact version can contain one or more files. Single-file assets use
the file SHA-256 as the version hash. Multi-file resources use a canonical JSON
manifest hash, with every member file individually hashed.

```sql
CREATE TABLE public.artifact_assets (
    asset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    asset_path TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    format TEXT NOT NULL
        CHECK (format IN (
            'safetensors', 'onnx', 'json', 'jsonl', 'parquet',
            'npy', 'npz', 'txt', 'vocab'
        )),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    storage_uri TEXT NOT NULL DEFAULT '',
    compression TEXT NOT NULL DEFAULT '',
    mmap_safe BOOLEAN NOT NULL DEFAULT FALSE,
    loader_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id, asset_path),
    UNIQUE (version_id, sha256)
);
```

Rules:

- `storage_uri` may point to S3, but the runtime must ignore any object whose
  bytes do not hash to `sha256`.
- For `npz`, loaders must pass `allow_pickle=False`.
- `mmap_safe=true` is only allowed for formats whose loader supports pure-data
  memory mapping without code execution.

### 3. Add resource metadata

```sql
CREATE TABLE public.state_artifact_metadata (
    version_id UUID PRIMARY KEY REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    resource_family TEXT NOT NULL DEFAULT '',
    language_tags TEXT[] NOT NULL DEFAULT '{}',
    vocabulary_size INTEGER,
    embedding_dim INTEGER,
    max_sequence_length INTEGER,
    label_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    training_data_summary TEXT NOT NULL DEFAULT '',
    provenance_summary TEXT NOT NULL DEFAULT '',
    intended_use TEXT NOT NULL DEFAULT '',
    limitations TEXT[] NOT NULL DEFAULT '{}',
    legal_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    deterministic_output_precision INTEGER NOT NULL DEFAULT 6,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

This table is the state artifact equivalent of code-facing IO specs and
parameters. It gives the catalog enough structure to search and bind resources
without loading the bytes.

### 4. Add logic-to-state compatibility contracts

Logic atoms should declare accepted state artifact ports. CDGs or higher-level
bundles bind those ports to exact state artifact versions.

```sql
CREATE TABLE public.artifact_state_ports (
    state_port_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES public.artifacts(artifact_id)
        ON DELETE CASCADE,
    version_id UUID REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    port_name TEXT NOT NULL,
    type_desc TEXT NOT NULL,
    accepted_formats TEXT[] NOT NULL DEFAULT '{}',
    required_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    ordinal INTEGER NOT NULL DEFAULT 0,
    UNIQUE (artifact_id, version_id, port_name)
);
```

Example: an NER wrapper declares a `model_resource` port requiring
`format in ['onnx']`, `label_schema.kind='BIO_NER'`, and
`max_sequence_length <= 512`.

### 5. Add immutable dependency pins

```sql
CREATE TABLE public.artifact_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dependent_version_id UUID NOT NULL REFERENCES public.artifact_versions(version_id)
        ON DELETE CASCADE,
    dependency_artifact_fqdn TEXT NOT NULL,
    dependency_content_hash TEXT NOT NULL CHECK (dependency_content_hash ~ '^[0-9a-f]{64}$'),
    dependency_role TEXT NOT NULL
        CHECK (dependency_role IN ('state_artifact', 'logic_atom', 'cdg')),
    port_name TEXT NOT NULL DEFAULT '',
    optional BOOLEAN NOT NULL DEFAULT FALSE,
    binding_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dependent_version_id, dependency_artifact_fqdn, dependency_content_hash, port_name)
);
```

For Tier 1 eligibility, all CDG bindings and logic atom default bindings must
pin `dependency_content_hash`. Unpinned FQDN-only dependencies are allowed only
for draft or exploratory tiers.

## Runtime Hydration

### Asset Resolver

Create a small runtime package, for example
`sciona_infra/assets/resolver.py` or a shared `sciona.assets` package if the CLI
runtime lives outside `sciona-infra`.

Core API:

```python
@dataclass(frozen=True)
class AssetRef:
    fqdn: str
    content_hash: str
    assets: tuple[AssetFile, ...]

class CryptographicIntegrityError(RuntimeError):
    pass

def hydrate_asset(ref: AssetRef, *, cache_dir: Path, network: bool) -> Path:
    ...

def resolve_asset_path(ref: AssetRef, *, cache_dir: Path, network: bool) -> Path:
    ...
```

Required behavior:

- Cache path is content-addressed:
  `~/.sciona/assets/sha256/<hash>/...`.
- Downloads write to a temp file, stream bytes through SHA-256, verify the
  expected hash, then atomically rename into the cache.
- Any mismatch raises `CryptographicIntegrityError` and deletes the temp file.
- S3 ETag is never treated as integrity proof.
- The resolver records a local manifest SQLite row with FQDN, content hash,
  byte size, verified timestamp, storage URI, and loader name.

### AOT Hydration

Add a command/workflow phase:

```bash
sciona hydrate <cdg-fqdn-or-receipt> --strict --cache-dir ~/.sciona/assets
```

The hydrator:

1. Reads `get_artifact_document(...)` for the CDG or receipt.
2. Traverses `artifact_dependencies` recursively.
3. Downloads every required state artifact.
4. Verifies all hashes.
5. Writes an offline execution lockfile listing every resolved content hash.

Enterprise execution policy:

- `--strict` mode fails if any required asset is missing locally.
- Tier 1 CDG execution should default to strict mode in air-gapped contexts.
- Lazy network hydration remains acceptable for development and lower trust
  tiers, but the same hash checks still apply.

## Secure Loader Policy

Add a loader allowlist used by publish validation, audit workers, and runtime:

| Format | Loader rule |
|---|---|
| `safetensors` | Use Safetensors APIs only. No PyTorch pickle path. |
| `onnx` | Load with ONNX Runtime. Disable custom op libraries by default. |
| `json` / `jsonl` | Parse with standard JSON parser and schema validation. |
| `parquet` | Read with PyArrow using explicit schema checks. |
| `npy` / `npz` | Use NumPy with `allow_pickle=False`; require dtype allowlist. |
| `txt` / `vocab` | UTF-8 text only, normalized line endings. |

Blocked in verified tiers:

- `.pkl`, `.pickle`
- `torch.load(...)` artifacts
- `joblib`
- arbitrary Python modules as resources
- ONNX custom ops unless separately registered as audited logic artifacts

Automated publish validation should inspect both file extension and magic bytes.
A renamed pickle file must still fail.

## Determinism Contract For Logic Atoms

Stateful NLP wrappers must satisfy these rules before Tier 2 verification:

- Accept state resources as explicit typed inputs or dependency-injected handles.
  They must not reach directly into global paths or mutable URLs.
- Set all random seeds for heuristic fallbacks.
- Expose no default stochastic behavior.
- Lock tokenizer normalization and pre/post-processing options in metadata.
- Set inference temperatures or sampling parameters to deterministic values.
- Round floating-point confidence values to the precision declared in
  `state_artifact_metadata.deterministic_output_precision`.
- Emit Sciona-standard labels rather than raw library tags.

Example output contract:

```json
{
  "entity_type": "Organization",
  "confidence": 0.9825,
  "span_start": 12,
  "span_end": 27
}
```

The wrapper can keep the raw library label internally, but public output must be
typed and de-jargonized.

## Audit And Tier Gates

### New audit evidence types

Extend `artifact_audit_evidence.audit_type` with:

- `asset_integrity_check`
- `format_security_scan`
- `loader_policy_check`
- `provenance_review`
- `license_ip_review`
- `privacy_review`
- `golden_eval`
- `determinism_replay`
- `boundary_review`

### Tier 2 Verified gate

Automated checks:

- All assets hash to declared SHA-256 values.
- File formats are allowlisted and magic bytes match metadata.
- Blocked serializers are absent.
- Loader can materialize the resource in a sandbox without network access.
- Logic atom can run a visible golden suite with deterministic outputs.
- Cross-run replay on the same hardware is byte-identical after declared
  rounding.

### Tier 1 Certified/Insurable gate

Manual expert review:

- Certify training-data provenance, legal basis, and license compatibility.
- Certify no known un-anonymized HIPAA/PII or restricted copyrighted material in
  the submitted training/resource pipeline.
- Review de-jargonized contracts and label mappings.
- Sign off on hidden golden evaluation suites.
- Define bounded-use limitations in metadata, for example:
  "Accuracy degrades for text exceeding 512 tokens."

Policy effect:

- LaaS coverage applies only when execution receipts show the exact certified
  logic atom content hash, exact state artifact content hash, and inputs within
  certified boundaries.
- Execution outside declared limits remains usable but is not covered by the
  Tier 1 policy.

## API And Registry Work

### Artifact publish endpoint

Add artifact-level publish endpoints instead of overloading
`POST /atoms`:

- `POST /artifacts/state`
- `POST /artifacts/{fqdn}/versions`
- `POST /artifacts/{fqdn}/assets/presign`
- `POST /artifacts/{fqdn}/versions/{version_id}/verify-assets`

Initial request model fields:

- `fqdn`
- `semver`
- `description`
- `resource_family`
- `language_tags`
- `assets`
- `metadata`
- `provenance`
- `legal_basis`
- `declared_sha256` or canonical manifest hash

The server must recompute hashes from uploaded bytes or from an internal
verified ingestion worker. Client-supplied hashes are claims, not proof.

### Document serving

Extend `get_artifact_document(...)` to include:

- `assets`
- `state_metadata`
- `state_ports`
- `dependencies`

Extend `catalog_artifacts_served` with enough state artifact fields for search:

- `artifact_kind`
- `resource_family`
- `language_tags`
- `accepted_formats`
- `trust_readiness`

## Implementation Phases

### Phase 0: Decisions And Fixtures

Deliverables:

- Decide whether multi-file `artifact_versions.content_hash` is a canonical
  manifest hash or a Merkle root. Recommendation: canonical JSON manifest hash
  first; Merkle roots can be added later.
- Define the exact JSON schema for multi-file manifests.
- Choose initial allowed formats: `safetensors`, `onnx`, `json`, `jsonl`,
  `parquet`, `npy`, `npz`, `txt`, `vocab`.
- Add two tiny test resources: a JSON taxonomy and a small ONNX or dummy
  Safetensors fixture.

Acceptance:

- Design fixtures have stable SHA-256 hashes checked into tests.

### Phase 1: Schema And RPCs

Deliverables:

- Migration adding `state_artifact`, `artifact_assets`,
  `state_artifact_metadata`, `artifact_state_ports`, and
  `artifact_dependencies`.
- Extend audit evidence enum.
- Update `artifact_is_publishable(...)` for state artifacts:
  - at least one asset row
  - state metadata row
  - de-jargonized description
  - references/provenance
  - audit rollup
- Extend `get_artifact_document(...)`.
- Add local Supabase integration tests.

Acceptance:

- State artifacts can be inserted, queried, and served through existing artifact
  document RPCs.
- A state artifact without asset rows is not publishable.

### Phase 2: Publish And Verification Pipeline

Deliverables:

- Pydantic models for state artifact publish requests.
- Artifact publish routes with presigned upload or service-side ingestion.
- Hash recomputation worker.
- Format scanner and loader policy checker.
- Audit evidence writes for integrity and format checks.

Acceptance:

- Uploading bytes with a wrong declared hash fails.
- Renamed pickle/joblib/torch artifacts fail scanner checks.
- Valid JSON/Parquet/ONNX/Safetensors fixtures produce verified asset rows.

### Phase 3: Runtime Hydration

Deliverables:

- Content-addressed cache under `~/.sciona/assets/sha256/<hash>`.
- `CryptographicIntegrityError`.
- Streaming download and atomic cache writes.
- Offline strict mode.
- AOT hydrator command or worker activity.
- Cache manifest SQLite table.

Acceptance:

- Hydration succeeds for valid fixture hashes.
- Tampered S3/local bytes fail before loader execution.
- Strict mode performs no network access and fails on cache miss.

### Phase 4: Logic Atom State Ports

Deliverables:

- Contribution contract update requiring explicit state ports for stateful NLP
  wrappers.
- Register metadata sidecar or decorator extension for state ports.
- Example NER/token taxonomy wrapper that accepts a hydrated resource handle.
- Sciona-standard output label mapping.

Acceptance:

- The example wrapper has no hardcoded URL or global resource path.
- CDG binding pins the exact state artifact content hash.
- Golden outputs are stable after declared rounding.

### Phase 5: Audit And LaaS Policy

Deliverables:

- Audit templates for provenance, license/IP, privacy, boundary, and hidden
  golden suite review.
- Rollup logic that blocks Tier 1 if any dependency hash is unpinned.
- Execution receipt fields for state artifact hashes and hydration mode.
- Policy checks that void Tier 1 coverage when certified limits are exceeded.

Acceptance:

- A CDG with unpinned or mutable state dependencies cannot reach Tier 1.
- A certified CDG receipt records logic atom hashes, state artifact hashes, and
  boundary metadata.

## Test Plan

Schema:

- Migration creates all tables and indexes.
- Publishability rejects incomplete state artifacts.
- `get_artifact_document(...)` returns assets, metadata, ports, dependencies.

Security:

- Pickle, joblib, and torch serialized fixtures are rejected.
- Extension spoofing is rejected by magic-byte scan.
- NumPy arrays load only with `allow_pickle=False`.

Runtime:

- Cache hit path never touches network.
- Cache miss path verifies hash before moving files into place.
- Hash mismatch raises `CryptographicIntegrityError`.
- Concurrent hydration of the same hash is atomic.

Determinism:

- Same input/resource pair produces identical output across repeated runs.
- Confidence rounding is enforced.
- Hidden golden suite failures block Tier 1 rollup.

Enterprise:

- AOT hydration lockfile is sufficient for strict offline execution.
- Receipts include all state artifact content hashes.

## Open Questions

- Should very large multi-file model families be represented as one
  `state_artifact` version with many `artifact_assets`, or as a parent CDG-like
  resource that depends on smaller state artifacts?
- Should ONNX custom ops be banned entirely for Tier 1, or allowed only when the
  custom op library is itself a separately audited logic artifact?
- Where should the shared runtime resolver live if the CLI/runtime is outside
  `sciona-infra`: `sciona.assets`, `sciona_infra.assets`, or a small independent
  package?
- Should state artifact hashes be signed by Sciona in addition to SHA-256
  pinning? Hash pinning proves integrity; signatures would prove registry
  attestation and may simplify enterprise audits.

## First Vertical Slice

Implement one minimal end-to-end example before broad NLP support:

1. Publish `sciona.resources.nlp.org_taxonomy.en.v1` as a JSON state artifact.
2. Publish `sciona.atoms.nlp.taxonomy_entity_match` as a logic atom with a
   `taxonomy` state port.
3. Bind them in a small CDG with the exact taxonomy content hash.
4. Hydrate the taxonomy into `~/.sciona/assets`.
5. Run the golden suite and emit a receipt containing both the logic atom hash
   and taxonomy hash.

This validates the registry split, hash pinning, no-pickle policy, local cache,
AOT hydration, typed NLP outputs, and audit evidence flow without waiting on a
large model artifact.
