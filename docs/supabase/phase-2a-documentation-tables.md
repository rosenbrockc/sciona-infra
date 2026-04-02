# Phase 2A: Documentation Tables (IO Specs, Parameters, Descriptions)

## Overview

Phase 2A creates the three documentation-pillar tables that describe what an atom does at the interface level: its data-flow ports (`atom_io_specs`), its callable signature (`atom_parameters`), and its human-readable descriptions (`atom_descriptions`). These tables, together with `atom_references` and `atom_audit_rollups` from other phases, feed the `atom_is_publishable()` gate that controls whether an atom appears in the served catalog.

After schema creation, four backfill scripts populate the tables from existing file-based sources:

| Table | Backfill source | Script |
|---|---|---|
| `atom_io_specs` | CDG JSON files (`ageoa/**/cdg.json`) | `scripts/backfill_io_specs.py` |
| `atom_parameters` | `audit_manifest.json` → `argument_details` | `scripts/backfill_parameters.py` |
| `atom_descriptions` (technical) | `audit_manifest.json` → `docstring_summary` + `atoms.description` | `scripts/backfill_technical_descriptions.py` |
| `atom_descriptions` (dejargonized) | LLM generation pass | `scripts/backfill_dejargonized_descriptions.py` |

**Dependency**: Phase 1 must be complete (the `atoms` table and `atom_versions` table must exist and be populated with the 505 atoms from the manifest).

**Parallelism**: Phase 2A can run in parallel with Phases 2B (References), 2C (Audit Evidence), 2D (Uncertainty/Verification), and 2E (Intrinsic Fields). All depend only on Phase 1.

---

## 1. Schema DDL

> **REFERENCE ONLY** — All tables, indexes, triggers, and RLS policies below are
> already created in **Phase 0** (with triggers DISABLED). Do NOT execute the DDL
> in this section. It is included here for documentation so each phase plan is
> self-contained. **Skip directly to Section 2 (Backfill Scripts) when executing
> this phase.**

### 1.1 `atom_io_specs`

Describes conceptual data-flow ports from CDG node decomposition. For leaf atoms these overlap 1:1 with the callable signature; for macro atoms with children, io_specs describe top-level ports.

```sql
-- ============================================================
-- ATOM IO SPECIFICATIONS
-- ============================================================

CREATE TABLE public.atom_io_specs (
    io_spec_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id    UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    name          TEXT NOT NULL,
    type_desc     TEXT NOT NULL DEFAULT 'Any',
    constraints   TEXT NOT NULL DEFAULT '',
    required      BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    ordinal       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (atom_id, version_id, direction, name)
);

CREATE INDEX idx_atom_io_specs_atom ON public.atom_io_specs (atom_id);
CREATE INDEX idx_atom_io_specs_version ON public.atom_io_specs (version_id);
```

### 1.2 `atom_parameters`

Documents the actual Python callable signature. Distinct from `hyperparams` (which describes tunable search-space metadata for optimization). Every published atom must have at least one row here.

```sql
-- ============================================================
-- ATOM PARAMETERS (callable signature documentation)
-- ============================================================

CREATE TABLE public.atom_parameters (
    parameter_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    version_id     UUID REFERENCES public.atom_versions(version_id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    position       INTEGER NOT NULL DEFAULT 0,
    kind           TEXT NOT NULL
                   CHECK (kind IN ('positional_only', 'positional_or_keyword', 'keyword_only', 'varargs', 'kwargs')),
    type_desc      TEXT NOT NULL DEFAULT 'Any',
    required       BOOLEAN NOT NULL DEFAULT TRUE,
    default_value_repr TEXT NOT NULL DEFAULT '',
    technical_description TEXT NOT NULL DEFAULT '',
    dejargonized_description TEXT NOT NULL DEFAULT '',
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (atom_id, version_id, name)
);

CREATE INDEX idx_atom_parameters_atom ON public.atom_parameters (atom_id);
CREATE INDEX idx_atom_parameters_version ON public.atom_parameters (version_id);
```

### 1.3 `atom_descriptions`

Stores multiple description variants per atom. The `technical` kind is backfilled from existing data; `dejargonized` requires LLM generation with a jargon score threshold.

```sql
-- ============================================================
-- ATOM DESCRIPTIONS (dejargonized / enriched)
-- ============================================================

CREATE TABLE public.atom_descriptions (
    description_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('technical', 'dejargonized', 'conceptual_summary', 'usage_example')),
    content        TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'en',
    generated_by   TEXT NOT NULL DEFAULT '',   -- e.g. 'llm:gpt-4o', 'human', 'ingest'
    reviewed       BOOLEAN NOT NULL DEFAULT FALSE,
    jargon_score   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, kind, language)
);

CREATE INDEX idx_atom_descriptions_atom ON public.atom_descriptions (atom_id);
```

### 1.4 Publishability Triggers (on these tables)

These triggers fire on changes to the three tables above and update `atoms.is_publishable`. The trigger function `refresh_atom_publishable()` and the check function `atom_is_publishable()` are created in Phase 1, but the trigger bindings for these specific tables are created here:

```sql
CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
```

### 1.5 RLS Policies

Enable RLS and apply read policies consistent with the atom visibility model:

```sql
ALTER TABLE public.atom_io_specs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_parameters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_descriptions ENABLE ROW LEVEL SECURITY;

-- Read: users can see documentation for atoms they can see
CREATE POLICY "io_specs_select" ON public.atom_io_specs
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            JOIN public.users u ON u.user_id = auth.uid()
            WHERE a.atom_id = atom_io_specs.atom_id
              AND (
                  a.visibility_tier = 'general'
                  OR u.effective_tier = 'internal'
                  OR (a.visibility_tier = 'early_access' AND u.effective_tier IN ('early_access', 'internal'))
              )
        )
    );

CREATE POLICY "parameters_select" ON public.atom_parameters
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            JOIN public.users u ON u.user_id = auth.uid()
            WHERE a.atom_id = atom_parameters.atom_id
              AND (
                  a.visibility_tier = 'general'
                  OR u.effective_tier = 'internal'
                  OR (a.visibility_tier = 'early_access' AND u.effective_tier IN ('early_access', 'internal'))
              )
        )
    );

CREATE POLICY "descriptions_select" ON public.atom_descriptions
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            JOIN public.users u ON u.user_id = auth.uid()
            WHERE a.atom_id = atom_descriptions.atom_id
              AND (
                  a.visibility_tier = 'general'
                  OR u.effective_tier = 'internal'
                  OR (a.visibility_tier = 'early_access' AND u.effective_tier IN ('early_access', 'internal'))
              )
        )
    );

-- Write: service role only (backfill and pipeline use service key)
-- No INSERT/UPDATE/DELETE policies for anon or authenticated roles.
```

---

## 2. Backfill Scripts

### 2.1 Backfill IO Specs from CDG JSON Files

**Source**: `ageoa/**/cdg.json` files in the `ageo-atoms` repo (currently ~85 CDG files).

**Script**: `scripts/backfill_io_specs.py`

**Logic**:

1. Glob for all `cdg.json` files under `ageoa/`.
2. For each CDG file, parse `nodes[]`.
3. Filter to nodes where `status == "atomic"` (leaf nodes that correspond to actual atoms).
4. Derive the atom FQDN from the CDG path and node name. The convention is `ageoa.<family>.<subfamily>.<node_name>` — use the directory structure relative to `ageoa/` as the dotted module path, and the node's `name` field as the symbol.
5. Look up the atom in Supabase by FQDN to get `atom_id`.
6. For each entry in `node.inputs[]`, insert an `atom_io_specs` row with `direction = 'input'`.
7. For each entry in `node.outputs[]`, insert an `atom_io_specs` row with `direction = 'output'`.

**Field mapping (CDG node input/output to `atom_io_specs`)**:

| CDG field | `atom_io_specs` column | Notes |
|---|---|---|
| `spec.name` | `name` | |
| `spec.type_desc` | `type_desc` | Falls back to `'Any'` |
| `spec.constraints` | `constraints` | Falls back to `''` |
| (derived) | `required` | `TRUE` for inputs without defaults, always `TRUE` for outputs |
| (derived) | `default_value_repr` | `''` (CDG does not carry defaults) |
| loop index `i` | `ordinal` | Preserves declaration order |
| (none) | `version_id` | `NULL` for initial backfill (version-independent) |

**Edge cases**:

- CDG nodes with `status != "atomic"` (i.e., `decomposed` root nodes) are skipped — they have empty `inputs`/`outputs` arrays.
- Some atoms may not have a CDG file (hand-written atoms, atoms from families without CDGs). These will have no `atom_io_specs` rows and will not pass the publishability gate until IO specs are added manually or via a future pipeline run.
- Duplicate CDG files (e.g., `d12` variants): each produces rows for its own atom FQDN. The UNIQUE constraint `(atom_id, version_id, direction, name)` prevents duplicates.

**Cross-validation**: For leaf atoms, the CDG input names should match the `argument_names` array in `audit_manifest.json`. The script should log warnings where they diverge.

```python
# scripts/backfill_io_specs.py
import json
import logging
from pathlib import Path
from supabase import create_client

log = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Pre-load atom FQDN -> atom_id mapping
atoms_resp = supabase.table("atoms").select("atom_id, fqdn").execute()
atom_lookup = {row["fqdn"]: row["atom_id"] for row in atoms_resp.data}

# Pre-load manifest argument_names for cross-validation
with open("data/audit_manifest.json") as f:
    manifest = json.load(f)
manifest_args = {
    entry["atom_name"]: entry.get("argument_names", [])
    for entry in manifest["atoms"]
}

stats = {"inserted": 0, "skipped_no_atom": 0, "cdg_files": 0, "cross_val_warnings": 0}

for cdg_path in sorted(Path("ageoa").rglob("cdg.json")):
    stats["cdg_files"] += 1
    cdg = json.loads(cdg_path.read_text())

    for node in cdg.get("nodes", []):
        if node.get("status") != "atomic":
            continue

        # Derive FQDN: ageoa.<path_parts>.<node_name>
        rel_parts = cdg_path.parent.relative_to("ageoa").parts
        module_prefix = "ageoa." + ".".join(rel_parts)
        node_name = node["name"]
        atom_fqdn = f"{module_prefix}.{node_name}"

        atom_id = atom_lookup.get(atom_fqdn)
        if not atom_id:
            # Try alternate FQDN derivations if the first doesn't match
            log.warning("No atom found for FQDN %s (CDG: %s)", atom_fqdn, cdg_path)
            stats["skipped_no_atom"] += 1
            continue

        # Cross-validate input names against manifest argument_names
        cdg_input_names = [s["name"] for s in node.get("inputs", [])]
        manifest_arg_names = manifest_args.get(atom_fqdn, [])
        if manifest_arg_names and cdg_input_names != manifest_arg_names:
            log.warning(
                "Input name mismatch for %s: CDG=%s, manifest=%s",
                atom_fqdn, cdg_input_names, manifest_arg_names,
            )
            stats["cross_val_warnings"] += 1

        rows = []
        for i, spec in enumerate(node.get("inputs", [])):
            rows.append({
                "atom_id": atom_id,
                "direction": "input",
                "name": spec["name"],
                "type_desc": spec.get("type_desc", "Any"),
                "constraints": spec.get("constraints", ""),
                "required": True,
                "default_value_repr": "",
                "ordinal": i,
            })
        for i, spec in enumerate(node.get("outputs", [])):
            rows.append({
                "atom_id": atom_id,
                "direction": "output",
                "name": spec["name"],
                "type_desc": spec.get("type_desc", "Any"),
                "constraints": spec.get("constraints", ""),
                "required": True,
                "default_value_repr": "",
                "ordinal": i,
            })

        if rows:
            supabase.table("atom_io_specs").upsert(rows).execute()
            stats["inserted"] += len(rows)

log.info("IO specs backfill complete: %s", stats)
```

### 2.2 Backfill Parameters from `audit_manifest.json`

**Source**: `data/audit_manifest.json` in the `ageo-atoms` repo. Each atom entry has an `argument_details` array.

**Script**: `scripts/backfill_parameters.py`

**Source data structure** (from `audit_manifest.json`):

```json
{
  "atom_name": "ageoa.advancedvi.core.evaluate_log_probability_density",
  "argument_details": [
    {
      "name": "q",
      "required": true,
      "kind": "positional_or_keyword",
      "annotation": "np.ndarray"
    },
    {
      "name": "z",
      "required": true,
      "kind": "positional_or_keyword",
      "annotation": "np.ndarray"
    }
  ],
  "return_annotation": "float",
  "uses_varargs": false,
  "uses_kwargs": false,
  "docstring_summary": "Compute a Gaussian log density from location-scale parameters."
}
```

**Field mapping (`argument_details[]` to `atom_parameters`)**:

| Manifest field | `atom_parameters` column | Notes |
|---|---|---|
| `arg.name` | `name` | |
| loop index `i` | `position` | Preserves declaration order |
| `arg.kind` | `kind` | Already matches CHECK constraint values: `positional_or_keyword`, `keyword_only`, etc. |
| `arg.annotation` | `type_desc` | Falls back to `'Any'` if missing or empty |
| `arg.required` | `required` | |
| (not in manifest) | `default_value_repr` | `''` — not available in manifest; future enhancement can parse from source |
| (not in manifest) | `technical_description` | `''` — can be derived from docstring Args section in a future pass |
| (not in manifest) | `dejargonized_description` | `''` — requires LLM generation pass (see Section 2.4) |
| (not in manifest) | `constraints_json` | `'{}'` — no structured constraint data in manifest |
| (none) | `version_id` | `NULL` for initial backfill |

**Additional considerations**:

- The manifest also provides `uses_varargs` and `uses_kwargs` booleans. If `uses_varargs` is true, the script should append a synthetic `*args` parameter row with `kind = 'varargs'`. If `uses_kwargs` is true, append `**kwargs` with `kind = 'kwargs'`.
- The `return_annotation` field is not stored in `atom_parameters` (it describes the return type, not a parameter). It can be stored as an `atom_io_specs` output row if not already covered by CDG data.
- 505 atoms in the manifest, each with 0-N argument_details entries.

```python
# scripts/backfill_parameters.py
import json
import logging
from supabase import create_client

log = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

with open("data/audit_manifest.json") as f:
    manifest = json.load(f)

atoms_resp = supabase.table("atoms").select("atom_id, fqdn").execute()
atom_lookup = {row["fqdn"]: row["atom_id"] for row in atoms_resp.data}

stats = {"inserted": 0, "skipped_no_atom": 0, "atoms_processed": 0}

for atom_entry in manifest["atoms"]:
    fqdn = atom_entry["atom_name"]
    atom_id = atom_lookup.get(fqdn)
    if not atom_id:
        log.warning("No atom found for FQDN %s", fqdn)
        stats["skipped_no_atom"] += 1
        continue

    stats["atoms_processed"] += 1
    rows = []

    for i, arg in enumerate(atom_entry.get("argument_details", [])):
        rows.append({
            "atom_id": atom_id,
            "name": arg["name"],
            "position": i,
            "kind": arg.get("kind", "positional_or_keyword"),
            "type_desc": arg.get("annotation", "Any") or "Any",
            "required": arg.get("required", True),
            "default_value_repr": "",
            "technical_description": "",
            "dejargonized_description": "",
            "constraints_json": {},
        })

    # Synthetic entries for varargs/kwargs
    next_pos = len(atom_entry.get("argument_details", []))
    if atom_entry.get("uses_varargs"):
        rows.append({
            "atom_id": atom_id,
            "name": "*args",
            "position": next_pos,
            "kind": "varargs",
            "type_desc": "Any",
            "required": False,
            "default_value_repr": "",
            "technical_description": "",
            "dejargonized_description": "",
            "constraints_json": {},
        })
        next_pos += 1
    if atom_entry.get("uses_kwargs"):
        rows.append({
            "atom_id": atom_id,
            "name": "**kwargs",
            "position": next_pos,
            "kind": "kwargs",
            "type_desc": "Any",
            "required": False,
            "default_value_repr": "",
            "technical_description": "",
            "dejargonized_description": "",
            "constraints_json": {},
        })

    if rows:
        supabase.table("atom_parameters").upsert(rows).execute()
        stats["inserted"] += len(rows)

log.info("Parameters backfill complete: %s", stats)
```

### 2.3 Backfill Technical Descriptions

**Source**: `audit_manifest.json` fields `docstring_summary` and the existing `atoms.description` column in Supabase.

**Script**: `scripts/backfill_technical_descriptions.py`

**Logic**:

1. For each atom in the manifest, prefer `docstring_summary` (richer, extracted from the actual docstring).
2. Fall back to the `atoms.description` column already in Supabase.
3. Skip atoms with no content from either source.
4. Insert into `atom_descriptions` with `kind = 'technical'`, `language = 'en'`, `jargon_score = 1.0` (technical descriptions are jargon by definition).

**Field mapping**:

| Source | `atom_descriptions` column | Notes |
|---|---|---|
| `atom_entry.docstring_summary` or `atoms.description` | `content` | Prefer docstring_summary; fall back to atoms.description |
| `'technical'` | `kind` | |
| `'en'` | `language` | |
| `'backfill-v1'` | `generated_by` | |
| `FALSE` | `reviewed` | |
| `1.0` | `jargon_score` | Technical descriptions are not dejargonized |

```python
# scripts/backfill_technical_descriptions.py
import json
import logging
from supabase import create_client

log = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

with open("data/audit_manifest.json") as f:
    manifest = json.load(f)

# Load atoms with their existing description field
atoms_resp = supabase.table("atoms").select("atom_id, fqdn, description").execute()
atom_lookup = {row["fqdn"]: row for row in atoms_resp.data}

stats = {"inserted": 0, "skipped_no_content": 0, "skipped_no_atom": 0}

rows = []
for atom_entry in manifest["atoms"]:
    fqdn = atom_entry["atom_name"]
    atom_row = atom_lookup.get(fqdn)
    if not atom_row:
        stats["skipped_no_atom"] += 1
        continue

    # Prefer docstring_summary (richer); fall back to atoms.description
    content = atom_entry.get("docstring_summary", "") or atom_row.get("description", "")
    if not content:
        stats["skipped_no_content"] += 1
        continue

    rows.append({
        "atom_id": atom_row["atom_id"],
        "kind": "technical",
        "language": "en",
        "content": content,
        "generated_by": "backfill-v1",
        "reviewed": False,
        "jargon_score": 1.0,
    })

# Batch upsert
for batch_start in range(0, len(rows), 100):
    batch = rows[batch_start:batch_start + 100]
    supabase.table("atom_descriptions").upsert(batch).execute()
    stats["inserted"] += len(batch)

log.info("Technical descriptions backfill complete: %s", stats)
```

### 2.4 Backfill Dejargonized Descriptions (LLM Generation Pass)

**Source**: Generated by LLM from the technical description, atom source code, and CDG context.

**Script**: `scripts/backfill_dejargonized_descriptions.py`

This is the most complex backfill step because it requires LLM inference for each atom. It should be run after the technical descriptions backfill (Section 2.3) so the LLM has the technical description as input context.

**Logic**:

1. Query all atoms that have a `technical` description but no `dejargonized` description.
2. For each atom, assemble a prompt context:
   - Technical description (from `atom_descriptions` where `kind = 'technical'`)
   - Parameter names and types (from `atom_parameters`)
   - IO specs (from `atom_io_specs`)
   - Domain family (from `atoms.domain_tags`)
3. Call the LLM to generate a plain-language description accessible to non-specialists.
4. Compute or request a `jargon_score` (0.0 = no jargon, 1.0 = fully jargon).
5. Insert only if `jargon_score < 0.4` (the publication threshold from the migration plan). Rows with higher scores are still inserted but flagged for human review.

**Field mapping**:

| Source | `atom_descriptions` column | Notes |
|---|---|---|
| LLM output | `content` | Plain-language description |
| `'dejargonized'` | `kind` | |
| `'en'` | `language` | |
| `'llm:<model_id>'` (e.g., `'llm:gpt-4o'`) | `generated_by` | Track which model produced it |
| `FALSE` | `reviewed` | All LLM output starts unreviewed |
| LLM-assessed or heuristic | `jargon_score` | Must be < 0.4 for publication |

**Rate limiting and cost considerations**:

- 505 atoms at approximately 1 LLM call each.
- Use batch API if available to reduce cost.
- Implement retry with exponential backoff.
- Log all prompt/response pairs for auditability.
- Consider running a jargon-score validation pass separately (e.g., a second LLM call or a heuristic based on domain-specific term frequency).

**Prompt template** (sketch):

```
You are rewriting a technical description of a scientific computing function
for a non-specialist audience. The reader may be a product manager, a student
from a different discipline, or an investor evaluating the technology.

Technical description: {technical_content}
Parameters: {parameter_list}
Domain: {domain_tags}

Write a 2-4 sentence plain-language description. Avoid jargon. If a technical
term is unavoidable, define it in parentheses. Focus on what the function does
and why someone would use it.
```

```python
# scripts/backfill_dejargonized_descriptions.py
import json
import logging
from supabase import create_client

log = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Find atoms with technical but no dejargonized description
technical = supabase.table("atom_descriptions") \
    .select("atom_id, content") \
    .eq("kind", "technical") \
    .eq("language", "en") \
    .execute()

existing_dejarg = supabase.table("atom_descriptions") \
    .select("atom_id") \
    .eq("kind", "dejargonized") \
    .eq("language", "en") \
    .execute()
existing_ids = {row["atom_id"] for row in existing_dejarg.data}

atoms_needing_dejarg = [
    row for row in technical.data
    if row["atom_id"] not in existing_ids
]

stats = {"generated": 0, "below_threshold": 0, "above_threshold": 0, "errors": 0}

for row in atoms_needing_dejarg:
    atom_id = row["atom_id"]
    technical_content = row["content"]

    # Load parameter context
    params = supabase.table("atom_parameters") \
        .select("name, type_desc, kind") \
        .eq("atom_id", atom_id) \
        .order("position") \
        .execute()
    param_list = ", ".join(
        f"{p['name']}: {p['type_desc']}" for p in params.data
    )

    # Load atom metadata
    atom = supabase.table("atoms") \
        .select("fqdn, domain_tags") \
        .eq("atom_id", atom_id) \
        .single() \
        .execute()

    try:
        result = generate_dejargonized(
            technical_content=technical_content,
            parameter_list=param_list,
            domain_tags=atom.data.get("domain_tags", []),
            fqdn=atom.data["fqdn"],
        )

        jargon_score = result["jargon_score"]
        if jargon_score < 0.4:
            stats["below_threshold"] += 1
        else:
            stats["above_threshold"] += 1

        supabase.table("atom_descriptions").upsert({
            "atom_id": atom_id,
            "kind": "dejargonized",
            "language": "en",
            "content": result["content"],
            "generated_by": result["model_id"],
            "reviewed": False,
            "jargon_score": jargon_score,
        }).execute()
        stats["generated"] += 1

    except Exception:
        log.exception("Failed to generate dejargonized description for %s", atom_id)
        stats["errors"] += 1

log.info("Dejargonized descriptions backfill complete: %s", stats)
```

---

## 3. Validation Criteria

### 3.1 Schema Validation

- [ ] All three tables exist with correct columns, types, and constraints.
- [ ] All indexes are created.
- [ ] All three publishability triggers fire correctly (insert a test row, verify `atoms.is_publishable` updates).
- [ ] RLS policies allow authenticated reads for atoms matching the user's entitlement tier.
- [ ] RLS policies block writes from non-service roles.
- [ ] UNIQUE constraints prevent duplicate rows.

### 3.2 IO Specs Backfill Validation

- [ ] Row count: at least one `atom_io_specs` row exists for every atom that has a CDG file with atomic nodes.
- [ ] Every row has a valid `atom_id` referencing an existing atom.
- [ ] No orphaned rows (all `atom_id` values exist in `atoms`).
- [ ] Direction values are only `'input'` or `'output'`.
- [ ] Ordinals are sequential within each `(atom_id, direction)` group.
- [ ] Cross-validation: for leaf atoms, input names match `argument_names` in the audit manifest. Log discrepancies; zero discrepancies is the target.

```sql
-- Count atoms with IO specs
SELECT COUNT(DISTINCT atom_id) FROM public.atom_io_specs;

-- Verify no orphaned rows
SELECT COUNT(*) FROM public.atom_io_specs ios
WHERE NOT EXISTS (SELECT 1 FROM public.atoms a WHERE a.atom_id = ios.atom_id);

-- Check ordinal continuity
SELECT atom_id, direction, array_agg(ordinal ORDER BY ordinal)
FROM public.atom_io_specs
GROUP BY atom_id, direction
HAVING array_agg(ordinal ORDER BY ordinal) != array_agg(generate_series(0, COUNT(*) - 1));
```

### 3.3 Parameters Backfill Validation

- [ ] Row count: at least one `atom_parameters` row exists for every atom in the manifest that has non-empty `argument_details`.
- [ ] Exactly 505 atoms processed (or close, accounting for any FQDN lookup misses).
- [ ] `kind` values are all within the CHECK constraint set.
- [ ] `position` values are sequential within each `atom_id`.
- [ ] No atoms have duplicate parameter names for the same `(atom_id, version_id)`.

```sql
-- Count atoms with parameters
SELECT COUNT(DISTINCT atom_id) FROM public.atom_parameters;

-- Verify kind values
SELECT DISTINCT kind FROM public.atom_parameters;

-- Check for atoms in manifest with argument_details but missing from atom_parameters
-- (run in backfill script as a post-validation step)
```

### 3.4 Technical Descriptions Backfill Validation

- [ ] Row count: one `atom_descriptions` row with `kind = 'technical'` for every atom that has either a `docstring_summary` in the manifest or a non-empty `atoms.description`.
- [ ] All rows have `jargon_score = 1.0` and `reviewed = FALSE`.
- [ ] No empty `content` values.

```sql
SELECT COUNT(*) FROM public.atom_descriptions WHERE kind = 'technical';

SELECT COUNT(*) FROM public.atom_descriptions
WHERE kind = 'technical' AND (content IS NULL OR content = '');
```

### 3.5 Dejargonized Descriptions Backfill Validation

- [ ] Row count: one `atom_descriptions` row with `kind = 'dejargonized'` for every atom that has a technical description.
- [ ] Rows with `jargon_score < 0.4` pass the publishability gate.
- [ ] Rows with `jargon_score >= 0.4` are flagged for human review (present but not blocking).
- [ ] `generated_by` field records the LLM model used.
- [ ] No empty `content` values.

```sql
-- Count by jargon threshold
SELECT
    COUNT(*) FILTER (WHERE jargon_score < 0.4) AS publishable,
    COUNT(*) FILTER (WHERE jargon_score >= 0.4) AS needs_review,
    COUNT(*) AS total
FROM public.atom_descriptions
WHERE kind = 'dejargonized';
```

### 3.6 Publishability Gate Integration Test

After all four backfills complete, verify the publishability gate works end-to-end:

```sql
-- Atoms that have all three Phase 2A pillars populated
SELECT COUNT(*) FROM public.atoms a
WHERE EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = a.atom_id)
  AND EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id)
  AND EXISTS (
      SELECT 1 FROM public.atom_descriptions d
      WHERE d.atom_id = a.atom_id
        AND d.kind = 'dejargonized'
        AND d.language = 'en'
        AND d.jargon_score < 0.4
  );

-- Cross-check against atoms.is_publishable (requires all 5 pillars from all phases)
SELECT a.fqdn, a.is_publishable,
    EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = a.atom_id) AS has_io_specs,
    EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id) AS has_params,
    EXISTS (SELECT 1 FROM public.atom_descriptions d WHERE d.atom_id = a.atom_id AND d.kind = 'dejargonized' AND d.jargon_score < 0.4) AS has_dejarg
FROM public.atoms a
WHERE a.status = 'approved'
ORDER BY a.fqdn
LIMIT 20;
```

---

## 4. Execution Order

Within Phase 2A, the scripts have a partial ordering:

```
1. Apply DDL migration (Section 1)           -- must be first
2. backfill_io_specs.py                       -- independent
3. backfill_parameters.py                     -- independent
4. backfill_technical_descriptions.py         -- independent
5. backfill_dejargonized_descriptions.py      -- depends on step 4 (needs technical descriptions as LLM input)
```

Steps 2, 3, and 4 can run in parallel. Step 5 must run after step 4.

---

## 5. Rollback Procedure

If any step fails or produces incorrect data, roll back in reverse order:

### 5.1 Rollback Backfill Data

```sql
-- Remove dejargonized descriptions (step 5)
DELETE FROM public.atom_descriptions WHERE kind = 'dejargonized' AND generated_by LIKE 'llm:%';

-- Remove technical descriptions (step 4)
DELETE FROM public.atom_descriptions WHERE kind = 'technical' AND generated_by = 'backfill-v1';

-- Remove parameters (step 3)
DELETE FROM public.atom_parameters WHERE version_id IS NULL;

-- Remove IO specs (step 2)
DELETE FROM public.atom_io_specs WHERE version_id IS NULL;
```

The `version_id IS NULL` predicate targets the initial backfill rows specifically, preserving any version-specific rows that may have been added by later pipeline runs.

### 5.2 Rollback Schema

If the entire phase must be rolled back:

```sql
-- Drop triggers first (they reference the tables)
DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;

-- Drop tables (CASCADE drops indexes and RLS policies)
DROP TABLE IF EXISTS public.atom_descriptions CASCADE;
DROP TABLE IF EXISTS public.atom_parameters CASCADE;
DROP TABLE IF EXISTS public.atom_io_specs CASCADE;
```

After rolling back the schema, `atoms.is_publishable` will remain at its last computed value. Run a manual refresh:

```sql
UPDATE public.atoms SET is_publishable = FALSE, updated_at = now();
```

---

## 6. Estimated Scope

| Item | Count | Notes |
|---|---|---|
| Atoms in manifest | 505 | All should get parameters + technical descriptions |
| CDG files | ~85 | Not all atoms have CDGs; IO specs coverage will be partial |
| Parameter rows (estimated) | ~1,200 | Average ~2.4 args per atom |
| IO spec rows (estimated) | ~400 | Only atoms with CDG files |
| Technical description rows | ~480 | Atoms with non-empty docstring_summary |
| Dejargonized description rows | ~480 | One per technical description |
| LLM calls for dejargonized | ~480 | Cost: ~$5-15 depending on model |

---

## 7. Open Questions

1. **FQDN derivation from CDG paths**: The exact mapping from CDG directory structure to atom FQDN needs validation against the full atom inventory. The current assumption (`ageoa.<dir_parts>.<node_name>`) may not match all 505 FQDNs in the manifest. The backfill script should log all misses for manual review.

2. **Parameter default values**: The audit manifest does not include default values for parameters. Should the backfill script parse the actual Python source files to extract defaults, or leave `default_value_repr` empty for now?

3. **Jargon score computation**: Should jargon scoring be done by the same LLM call that generates the dejargonized description (self-assessment), by a separate validation LLM call, or by a heuristic (e.g., domain-term frequency against a general English corpus)?

4. **Atoms without CDG files**: ~420 of the 505 atoms have no CDG file and will lack IO specs. Should the backfill derive IO specs from `argument_details` + `return_annotation` in the manifest as a fallback, treating parameters as input ports and the return type as an output port?

5. **Publishability trigger during backfill**: The publishability trigger fires on every INSERT. During bulk backfill this causes N redundant `atoms.is_publishable` recalculations. Consider temporarily disabling the triggers during backfill and running a single batch refresh afterward:
   ```sql
   ALTER TABLE public.atom_io_specs DISABLE TRIGGER trg_publishable_io_specs;
   ALTER TABLE public.atom_parameters DISABLE TRIGGER trg_publishable_parameters;
   ALTER TABLE public.atom_descriptions DISABLE TRIGGER trg_publishable_descriptions;
   -- ... run backfill scripts ...
   ALTER TABLE public.atom_io_specs ENABLE TRIGGER trg_publishable_io_specs;
   ALTER TABLE public.atom_parameters ENABLE TRIGGER trg_publishable_parameters;
   ALTER TABLE public.atom_descriptions ENABLE TRIGGER trg_publishable_descriptions;
   -- Batch refresh
   UPDATE public.atoms SET is_publishable = public.atom_is_publishable(atom_id), updated_at = now();
   ```
