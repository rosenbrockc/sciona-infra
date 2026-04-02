# Phase 2C: Academic References

## Overview

Phase 2C creates and populates the academic reference tables. There are two tables:

1. **`references_registry`** -- a global bibliography of all scholarly references known to the system, one row per unique reference (paper, book, repository, etc.). This replaces the file-based `data/references/registry.json`.
2. **`atom_references`** -- a join table linking atoms to references with per-atom attribution metadata (confidence, matched CDG nodes, relevance notes). This replaces the per-atom `ageoa/*/references.json` files.

The split mirrors the existing file-based architecture:
- `data/references/registry.json` holds ~85 canonical bibliographic records keyed by `ref_id` (schema v1.0).
- ~94 per-atom `references.json` files (schema v1.1) bind atoms to registry entries by `ref_id`, adding per-atom `match_metadata`.

### Dependencies

- **Requires Phase 1**: `atoms` table must exist with populated `atom_id` and `fqdn` columns.
- **Can run in parallel** with Phases 2A, 2B, 2D, 2E -- no cross-dependencies.

### Source data summary

**Central registry** (`data/references/registry.json`):
- 85 entries: 48 papers, 27 repositories, 10 books, 0 web/thesis/standard
- Fields: `ref_id`, `type`, `title`, `authors[]`, `year`, `venue`, `doi`, `url`, `bibtex_key`, `bibtex_raw`, `match_metadata`
- Schema validated by `data/references/schema.json` (JSON Schema draft 2020-12)
- `ref_id` pattern: `^[a-z][a-z0-9_]*[0-9]{4}[a-z]?$` (e.g., `almgren2000`, `repo_skyfield`)
- DOI present on ~10 entries; remainder use URL only

**Per-atom files** (`ageoa/**/references.json`, schema v1.1):
- ~94 files across atom families
- Atom keys use manifest_key format: `{fqdn}@{file_path}:{line}`
  - Example: `ageoa.algorithms.graph.bellman_ford@ageoa/algorithms/graph.py:174`
  - FQDN is the portion before the `@` character
- Each atom key maps to an object with `references[]` and `auto_attribution_runs[]`
- Each reference binding contains only `ref_id` and `match_metadata` (no bibliographic data)

**Existing tooling** (in ageo-atoms repo):
- `scripts/build_references.py` -- collects per-atom refs, validates against registry, syncs to hyperparams manifest, generates BibTeX
- `scripts/add_reference.py` -- CLI to add references via DOI resolution (CrossRef API) or manual entry

---

## 1. SQL DDL

> **REFERENCE ONLY** — All tables, indexes, and RLS policies below are already
> created in **Phase 0** (tables + indexes) and **Phase 3** (RLS policies). Do NOT
> execute the DDL or RLS statements in this section. They are included for
> documentation. **Skip directly to Section 2 (Backfill Scripts) when executing
> this phase.**

### 1.1 `references_registry`

Global bibliography table. One row per unique scholarly reference. Replaces `data/references/registry.json`.

```sql
-- ============================================================
-- REFERENCES REGISTRY (global bibliography)
-- ============================================================

CREATE TABLE public.references_registry (
    ref_id         TEXT PRIMARY KEY,
    ref_type       TEXT NOT NULL DEFAULT 'paper'
                   CHECK (ref_type IN ('paper', 'repository', 'web', 'book', 'thesis', 'standard')),
    title          TEXT NOT NULL,
    authors        TEXT[] NOT NULL DEFAULT '{}',
    year           INTEGER,
    venue          TEXT NOT NULL DEFAULT '',
    doi            TEXT,
    url            TEXT NOT NULL DEFAULT '',
    bibtex_key     TEXT NOT NULL DEFAULT '',
    bibtex_raw     TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_references_registry_doi
    ON public.references_registry (doi) WHERE doi IS NOT NULL;
```

Design notes:
- `ref_id` is the primary key, matching the `ref_id` slug pattern from the existing schema (`almgren2000`, `repo_skyfield`, etc.).
- `ref_type` uses the same enum as the JSON schema's `type` field. Column name avoids the SQL reserved word `type`.
- `doi` is nullable (only ~10 of 85 entries have DOIs). The partial unique index prevents duplicate DOIs when present.
- `bibtex_raw` stores the complete BibTeX entry for lossless round-tripping. Currently no entries have this populated, but `add_reference.py` fetches it from doi.org when adding new references.
- `bibtex_key` defaults to empty; when empty the application should fall back to `ref_id` for BibTeX generation.

### 1.2 `atom_references`

Join table linking atoms to references. Copied from Section 2.2 of the migration plan with one addition: a foreign key to `references_registry`.

```sql
-- ============================================================
-- ATOM REFERENCES (academic / scholarly)
-- ============================================================

CREATE TABLE public.atom_references (
    reference_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id        UUID NOT NULL REFERENCES public.atoms(atom_id) ON DELETE CASCADE,
    ref_id         TEXT NOT NULL REFERENCES public.references_registry(ref_id) ON DELETE CASCADE,
    -- ref_key is always populated: DOI when available, otherwise ref_id.
    -- This is the dedup key since DOI can be NULL.
    ref_key        TEXT NOT NULL,
    doi            TEXT,
    title          TEXT NOT NULL,
    authors        TEXT[] NOT NULL DEFAULT '{}',
    year           INTEGER,
    url            TEXT NOT NULL DEFAULT '',
    relevance_note TEXT NOT NULL DEFAULT '',
    confidence     TEXT NOT NULL DEFAULT ''
                   CHECK (confidence IN ('', 'low', 'medium', 'high')),
    -- Which CDG node IDs this reference is relevant to (empty = whole atom)
    matched_nodes  TEXT[] NOT NULL DEFAULT '{}',
    source         TEXT NOT NULL DEFAULT 'manual'
                   CHECK (source IN ('manual', 'llm_extracted', 'crossref', 'semantic_scholar')),
    verified       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, ref_key)
);

CREATE INDEX idx_atom_references_atom ON public.atom_references (atom_id);
CREATE INDEX idx_atom_references_doi  ON public.atom_references (doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_atom_references_ref  ON public.atom_references (ref_id);
```

Design notes:
- `atom_references` denormalizes bibliographic fields (`title`, `authors`, `year`, `doi`, `url`) from `references_registry` for query convenience. The `ref_id` FK maintains referential integrity.
- `ref_key` is the dedup key: DOI when available, otherwise the `ref_id` slug. The `UNIQUE (atom_id, ref_key)` constraint prevents duplicate bindings.
- The denormalized fields are populated at insert time from the registry. If the registry entry is later corrected, a maintenance script can propagate changes.

### 1.3 RLS policies

```sql
ALTER TABLE public.references_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.atom_references ENABLE ROW LEVEL SECURITY;

-- references_registry: readable by all authenticated users, writable by service role only
CREATE POLICY references_registry_select ON public.references_registry
    FOR SELECT TO authenticated
    USING (true);

CREATE POLICY references_registry_select_anon ON public.references_registry
    FOR SELECT TO anon
    USING (true);

-- atom_references: follows atom visibility
CREATE POLICY atom_references_select ON public.atom_references
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
        )
    );

CREATE POLICY atom_references_insert ON public.atom_references
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.atoms a
            WHERE a.atom_id = atom_references.atom_id
              AND a.owner_id = auth.uid()
        )
    );
```

### 1.4 Publishability trigger

The `atom_references` table participates in the `is_publishable` gate. The trigger function is created in Phase 1; the trigger binding is:

```sql
CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
```

---

## 2. Backfill Scripts

The backfill proceeds in two phases:
1. **Phase A**: Populate `references_registry` from `data/references/registry.json`.
2. **Phase B**: Populate `atom_references` by walking `ageoa/**/references.json`, resolving each `ref_id` against the now-populated registry, and looking up `atom_id` by FQDN.

### 2.1 Field mapping: `references_registry`

| `references_registry` column | Source field in `registry.json` | Notes |
|---|---|---|
| `ref_id` | Object key / `references[key].ref_id` | Primary key; always matches the object key |
| `ref_type` | `references[key].type` | `paper`, `repository`, `book`, etc. |
| `title` | `references[key].title` | Required by schema |
| `authors` | `references[key].authors` | Array of strings; may be empty for repositories |
| `year` | `references[key].year` | Integer or NULL |
| `venue` | `references[key].venue` | Journal/conference name; empty for repositories |
| `doi` | `references[key].doi` | NULL for ~75 of 85 entries |
| `url` | `references[key].url` | Required by schema (at least one of doi/url) |
| `bibtex_key` | `references[key].bibtex_key` | Defaults to `ref_id` if absent |
| `bibtex_raw` | `references[key].bibtex_raw` | Currently empty for all entries |

### 2.2 Field mapping: `atom_references`

| `atom_references` column | Source | Notes |
|---|---|---|
| `atom_id` | Lookup `atoms.atom_id` by FQDN extracted from atom key | See parsing logic below |
| `ref_id` | Per-atom `ref_binding.ref_id` | FK to `references_registry` |
| `ref_key` | `registry[ref_id].doi` if present, else `ref_id` | Dedup key |
| `doi` | `registry[ref_id].doi` | May be NULL |
| `title` | `registry[ref_id].title` | Denormalized from registry |
| `authors` | `registry[ref_id].authors` | Denormalized from registry |
| `year` | `registry[ref_id].year` | Denormalized from registry |
| `url` | `registry[ref_id].url` | Denormalized from registry |
| `relevance_note` | Per-atom `match_metadata.notes` | Per-atom override, NOT registry-level notes |
| `confidence` | Per-atom `match_metadata.confidence` | `"high"`, `"medium"`, `"low"`, or `""` |
| `matched_nodes` | Per-atom `match_metadata.matched_nodes` | Array of CDG node ID strings |
| `source` | Mapped from per-atom `match_metadata.match_type` | See mapping table below |
| `verified` | `False` | All backfilled rows start unverified |

### 2.3 Parsing the manifest_key format

Per-atom `references.json` files (schema v1.1) use the `atoms` dict with keys in `manifest_key` format:

```
{fqdn}@{file_path}:{line_number}
```

Examples:
```
ageoa.algorithms.graph.bellman_ford@ageoa/algorithms/graph.py:174
ageoa.biosppy.ecg_zz2018.calculatebeatagreementsqi@ageoa/biosppy/ecg_zz2018/atoms.py:40
ageoa.institutional_quant_engine.almgren_chriss_v2.optimalexecutiontrajectory@ageoa/institutional_quant_engine/almgren_chriss_v2/atoms.py:31
ageoa.tempo.offset_tai2tdb@ageoa/tempo.py:60
```

Parsing logic:
1. Split on `@` -- take the left side as the FQDN.
2. The right side (`file_path:line`) is informational and not used for database lookup.
3. The FQDN is used to look up `atoms.atom_id` via `SELECT atom_id FROM atoms WHERE fqdn = $1`.

Edge cases:
- If the FQDN contains no `@`, the entire string is treated as the FQDN (legacy v1.0 format).
- Some FQDNs may not exist in the `atoms` table yet (draft/deprecated atoms). These are logged and skipped.

### 2.4 `match_type` to `source` mapping

| `match_metadata.match_type` | `atom_references.source` |
|---|---|
| `"manual"` | `"manual"` |
| `"ast_subgraph"` | `"llm_extracted"` |
| `"name_heuristic"` | `"llm_extracted"` |
| absent / null | `"manual"` |

Both `ast_subgraph` and `name_heuristic` are automated attribution methods. They map to `llm_extracted` since there is no dedicated column for the sub-type. The original `match_type` value is preserved implicitly in `relevance_note` via the notes field.

### 2.5 `ref_key` resolution order

The `UNIQUE (atom_id, ref_key)` constraint requires a non-NULL, non-empty `ref_key`. Resolution:

1. If `registry[ref_id].doi` is present and non-empty: `ref_key = doi`
2. Otherwise: `ref_key = ref_id`
3. Fallback (should never happen given schema validation): `ref_key = title[:80]`

Two atoms referencing the same paper get separate rows (unique on `(atom_id, ref_key)`). This is correct -- the table models atom-to-reference bindings, not a global bibliography.

### 2.6 Script: `backfill_references_registry.py`

```python
#!/usr/bin/env python3
"""scripts/backfill_references_registry.py

Backfill the references_registry table from data/references/registry.json.

Must run BEFORE backfill_references.py (atom_references has FK to registry).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from supabase import create_client

log = logging.getLogger(__name__)

REGISTRY_PATH = Path("data/references/registry.json")


def load_registry(path: Path) -> dict[str, dict]:
    with open(path) as f:
        data = json.load(f)
    return data.get("references", {})


def backfill(supabase, registry_path: Path, *, dry_run: bool = False):
    registry = load_registry(registry_path)
    log.info("Loaded %d registry entries from %s", len(registry), registry_path)

    stats = {"upserted": 0, "errors": 0}

    for ref_id, entry in sorted(registry.items()):
        row = {
            "ref_id": ref_id,
            "ref_type": entry.get("type", "paper"),
            "title": entry.get("title", ""),
            "authors": entry.get("authors", []),
            "year": entry.get("year"),
            "venue": entry.get("venue", ""),
            "doi": entry.get("doi"),
            "url": entry.get("url", ""),
            "bibtex_key": entry.get("bibtex_key", ""),
            "bibtex_raw": entry.get("bibtex_raw", ""),
        }

        if dry_run:
            log.info("DRY RUN: would upsert registry entry %s", ref_id)
            stats["upserted"] += 1
            continue

        try:
            supabase.table("references_registry").upsert(
                row, on_conflict="ref_id"
            ).execute()
            stats["upserted"] += 1
        except Exception:
            log.exception("Failed to upsert registry entry %s", ref_id)
            stats["errors"] += 1

    log.info("Registry backfill complete: %s", stats)
    return stats


def main():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log.info("Running in dry-run mode")

    stats = backfill(supabase, registry_path=REGISTRY_PATH, dry_run=dry_run)

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
```

### 2.7 Script: `backfill_references.py`

```python
#!/usr/bin/env python3
"""scripts/backfill_references.py

Backfill atom_references from per-atom references.json files and the
references_registry table (must be populated first via backfill_references_registry.py).

Requires Phase 1 atoms table to be populated.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from supabase import create_client

log = logging.getLogger(__name__)

ATOMS_ROOT = Path("ageoa")
REGISTRY_PATH = Path("data/references/registry.json")

MATCH_TYPE_TO_SOURCE = {
    "manual": "manual",
    "ast_subgraph": "llm_extracted",
    "name_heuristic": "llm_extracted",
}


def load_registry(path: Path) -> dict[str, dict]:
    """Load the central bibliographic registry keyed by ref_id."""
    with open(path) as f:
        data = json.load(f)
    return data.get("references", data)


def extract_fqdn(atom_key: str) -> str:
    """Extract FQDN from a manifest_key like 'ageoa.foo.bar@ageoa/foo/atoms.py:19'.

    The manifest_key format is: {fqdn}@{file_path}:{line_number}
    Split on '@' and take the left side. If no '@' is present (legacy v1.0),
    the entire string is the FQDN.
    """
    at_idx = atom_key.find("@")
    if at_idx == -1:
        return atom_key
    return atom_key[:at_idx]


def resolve_atom_id(supabase, fqdn: str, cache: dict[str, str | None]) -> str | None:
    """Look up atom_id by FQDN, with a local cache to avoid repeated queries."""
    if fqdn in cache:
        return cache[fqdn]
    result = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    atom_id = result.data[0]["atom_id"] if result.data else None
    cache[fqdn] = atom_id
    return atom_id


def build_ref_key(registry_entry: dict) -> str:
    """Determine ref_key: DOI if available, otherwise ref_id, otherwise title[:80]."""
    doi = registry_entry.get("doi")
    if doi:
        return doi
    ref_id = registry_entry.get("ref_id")
    if ref_id:
        return ref_id
    return registry_entry.get("title", "unknown")[:80]


def map_source(match_metadata: dict) -> str:
    """Map match_metadata.match_type to atom_references.source."""
    match_type = match_metadata.get("match_type", "")
    return MATCH_TYPE_TO_SOURCE.get(match_type, "manual")


def backfill(supabase, atoms_root: Path, registry_path: Path, *, dry_run: bool = False):
    registry = load_registry(registry_path)
    log.info("Loaded %d registry entries", len(registry))

    fqdn_cache: dict[str, str | None] = {}
    stats = {
        "inserted": 0,
        "skipped_no_atom": 0,
        "skipped_no_registry": 0,
        "errors": 0,
    }

    refs_files = sorted(atoms_root.rglob("references.json"))
    log.info("Found %d per-atom references.json files", len(refs_files))

    for refs_path in refs_files:
        # Skip __pycache__ directories
        if "__pycache__" in refs_path.parts:
            continue

        with open(refs_path) as f:
            data = json.load(f)

        atoms_block = data.get("atoms", {})
        for atom_key, atom_data in atoms_block.items():
            fqdn = extract_fqdn(atom_key)
            atom_id = resolve_atom_id(supabase, fqdn, fqdn_cache)
            if not atom_id:
                log.warning("No atom found for FQDN: %s (from %s)", fqdn, refs_path)
                stats["skipped_no_atom"] += 1
                continue

            for ref_binding in atom_data.get("references", []):
                ref_id = ref_binding.get("ref_id", "")
                if not ref_id:
                    log.warning("Empty ref_id in %s for atom %s", refs_path, fqdn)
                    continue

                registry_entry = registry.get(ref_id)
                if not registry_entry:
                    log.warning("ref_id %r not in registry (atom %s)", ref_id, fqdn)
                    stats["skipped_no_registry"] += 1
                    continue

                match_meta = ref_binding.get("match_metadata", {})
                ref_key = build_ref_key(registry_entry)

                row = {
                    "atom_id": atom_id,
                    "ref_id": ref_id,
                    "ref_key": ref_key,
                    "doi": registry_entry.get("doi"),
                    "title": registry_entry.get("title", ""),
                    "authors": registry_entry.get("authors", []),
                    "year": registry_entry.get("year"),
                    "url": registry_entry.get("url", ""),
                    "relevance_note": match_meta.get("notes", ""),
                    "confidence": match_meta.get("confidence", ""),
                    "matched_nodes": match_meta.get("matched_nodes", []),
                    "source": map_source(match_meta),
                    "verified": False,
                }

                if dry_run:
                    log.info("DRY RUN: would upsert %s -> %s", fqdn, ref_key)
                    stats["inserted"] += 1
                    continue

                try:
                    supabase.table("atom_references").upsert(
                        row, on_conflict="atom_id,ref_key"
                    ).execute()
                    stats["inserted"] += 1
                except Exception:
                    log.exception("Failed to upsert ref %s for atom %s", ref_id, fqdn)
                    stats["errors"] += 1

    log.info("Backfill complete: %s", stats)
    return stats


def main():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        log.info("Running in dry-run mode")

    stats = backfill(
        supabase,
        atoms_root=ATOMS_ROOT,
        registry_path=REGISTRY_PATH,
        dry_run=dry_run,
    )

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
```

### 2.8 Batching considerations

The registry has ~85 entries; per-atom files total ~94 with typically 2-5 references per atom, yielding a few hundred `atom_references` rows. No batching or pagination is needed. Individual upserts are used for simplicity and error isolation. If the registry grows past 500 entries, switch to chunked upserts of 100 rows.

### 2.9 Execution order

```bash
# Step 1: Populate the global registry (no dependencies beyond the table existing)
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 scripts/backfill_references_registry.py

# Step 2: Populate atom-to-reference bindings (requires registry + atoms table)
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 scripts/backfill_references.py
```

---

## 3. Validation Queries

### 3.1 Registry row count

```sql
-- Total registry entries
SELECT COUNT(*) AS total_registry_entries FROM public.references_registry;
-- Expected: ~85 (compare with source file)

-- Breakdown by type
SELECT ref_type, COUNT(*) FROM public.references_registry GROUP BY ref_type ORDER BY count DESC;
-- Expected: paper ~48, repository ~27, book ~10
```

Compare against source:
```bash
python3 -c "
import json
data = json.load(open('data/references/registry.json'))
refs = data.get('references', {})
print(f'Total: {len(refs)}')
from collections import Counter
c = Counter(r.get('type', 'paper') for r in refs.values())
for t, n in c.most_common(): print(f'  {t}: {n}')
"
```

### 3.2 Atom references row count

```sql
-- Total atom-reference bindings
SELECT COUNT(*) AS total_refs FROM public.atom_references;

-- References per atom (top 20)
SELECT a.fqdn, COUNT(r.reference_id) AS ref_count
FROM public.atoms a
LEFT JOIN public.atom_references r ON r.atom_id = a.atom_id
GROUP BY a.fqdn
ORDER BY ref_count DESC
LIMIT 20;
```

Compare against source:
```bash
python3 -c "
import json
from pathlib import Path
count = 0
for p in sorted(Path('ageoa').rglob('references.json')):
    if '__pycache__' in p.parts: continue
    data = json.load(open(p))
    for atom_data in data.get('atoms', {}).values():
        count += len(atom_data.get('references', []))
print(f'Expected atom_references rows: {count}')
"
```

### 3.3 ref_key integrity

```sql
-- No NULL or empty ref_keys
SELECT COUNT(*) FROM public.atom_references WHERE ref_key IS NULL OR ref_key = '';
-- Expected: 0

-- ref_key = DOI for all rows that have a DOI
SELECT COUNT(*)
FROM public.atom_references
WHERE doi IS NOT NULL AND ref_key != doi;
-- Expected: 0

-- No duplicate (atom_id, ref_key) pairs (enforced by UNIQUE, but verify)
SELECT atom_id, ref_key, COUNT(*)
FROM public.atom_references
GROUP BY atom_id, ref_key
HAVING COUNT(*) > 1;
-- Expected: 0 rows
```

### 3.4 Foreign key integrity

```sql
-- All atom_ids exist in atoms table
SELECT COUNT(*)
FROM public.atom_references r
LEFT JOIN public.atoms a ON a.atom_id = r.atom_id
WHERE a.atom_id IS NULL;
-- Expected: 0

-- All ref_ids exist in references_registry
SELECT COUNT(*)
FROM public.atom_references ar
LEFT JOIN public.references_registry rr ON rr.ref_id = ar.ref_id
WHERE rr.ref_id IS NULL;
-- Expected: 0

-- No orphaned registry entries is NOT an error (registry entries
-- may exist without any atom binding them)
SELECT rr.ref_id
FROM public.references_registry rr
LEFT JOIN public.atom_references ar ON ar.ref_id = rr.ref_id
WHERE ar.ref_id IS NULL;
-- Informational only; orphaned registry entries are expected
```

### 3.5 CHECK constraint coverage

```sql
-- All confidence values are valid
SELECT DISTINCT confidence FROM public.atom_references;
-- Expected: subset of {'', 'low', 'medium', 'high'}

-- All source values are valid
SELECT DISTINCT source FROM public.atom_references;
-- Expected: subset of {'manual', 'llm_extracted', 'crossref', 'semantic_scholar'}

-- All ref_type values are valid
SELECT DISTINCT ref_type FROM public.references_registry;
-- Expected: subset of {'paper', 'repository', 'web', 'book', 'thesis', 'standard'}
```

### 3.6 Publishability gate

```sql
-- Atoms with references should have is_publishable re-evaluated
-- (the trigger fires on INSERT, so this happens automatically)
SELECT a.fqdn, a.is_publishable
FROM public.atoms a
WHERE EXISTS (SELECT 1 FROM public.atom_references r WHERE r.atom_id = a.atom_id)
  AND EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = a.atom_id)
  AND EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id)
  AND EXISTS (
      SELECT 1 FROM public.atom_descriptions d
      WHERE d.atom_id = a.atom_id AND d.kind = 'dejargonized'
        AND d.language = 'en' AND d.jargon_score < 0.4
  )
  AND EXISTS (SELECT 1 FROM public.atom_audit_rollups ar WHERE ar.atom_id = a.atom_id)
  AND a.is_publishable = FALSE;
-- Expected: 0 rows (all fully-documented atoms should be publishable)
```

### 3.7 RPC integration

```sql
-- Verify references appear in the atom document RPC
SELECT (public.get_atom_document('ageoa.algorithms.graph.bellman_ford'))->'references';
-- Expected: non-null JSON array with clrs2009 entry

-- Verify catalog index reference_count is populated
SELECT atom_id, reference_count
FROM public.catalog_atoms_index
WHERE reference_count > 0
LIMIT 10;
```

### 3.8 Denormalization consistency

```sql
-- Verify that denormalized fields in atom_references match the registry
SELECT ar.ref_id, ar.title AS ar_title, rr.title AS rr_title
FROM public.atom_references ar
JOIN public.references_registry rr ON rr.ref_id = ar.ref_id
WHERE ar.title != rr.title;
-- Expected: 0 rows
```

---

## 4. Rollback Procedure

### 4.1 Data-only rollback (keep schema)

If the backfill produces bad data but the schema is correct:

```sql
-- Delete all atom-reference bindings (all backfilled rows have verified = FALSE)
DELETE FROM public.atom_references WHERE verified = FALSE;

-- Delete all registry entries (or truncate for full wipe)
TRUNCATE public.references_registry CASCADE;
-- CASCADE will also delete atom_references rows due to FK
```

Then re-run both backfill scripts in order.

### 4.2 Full rollback (drop tables)

If the schema itself needs to change:

```sql
-- 1. Drop the publishability trigger first
DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;

-- 2. Drop RLS policies
DROP POLICY IF EXISTS atom_references_select ON public.atom_references;
DROP POLICY IF EXISTS atom_references_insert ON public.atom_references;
DROP POLICY IF EXISTS references_registry_select ON public.references_registry;
DROP POLICY IF EXISTS references_registry_select_anon ON public.references_registry;

-- 3. Drop atom_references first (depends on references_registry)
DROP TABLE IF EXISTS public.atom_references CASCADE;

-- 4. Drop references_registry
DROP TABLE IF EXISTS public.references_registry CASCADE;

-- 5. Refresh materialized view (reference_count will be 0)
REFRESH MATERIALIZED VIEW CONCURRENTLY public.catalog_atoms_index;
```

After dropping, all atoms will have `is_publishable = FALSE` since the references pillar is missing. Force a refresh if needed:

```sql
-- Force is_publishable re-evaluation for all atoms
UPDATE public.atoms SET updated_at = now();
```

### 4.3 Re-running after rollback

Both backfill scripts are idempotent (they use `upsert` with conflict keys). After a data-only rollback:

```bash
# Re-run in order
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 scripts/backfill_references_registry.py
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 scripts/backfill_references.py
```

---

## 5. Edge Cases and Decisions

### 5.1 Overlapping per-atom files

Some atom families have `references.json` at multiple directory levels (e.g., `ageoa/conjugate_priors/references.json` and `ageoa/conjugate_priors/beta_binom/references.json`). The more specific file may reference the same atom FQDN with the same ref_id. The `upsert` on `(atom_id, ref_key)` ensures the last write wins. Since the script walks files in sorted path order, deeper (more specific) files overwrite shallower ones, which is the desired behavior.

### 5.2 Registry entries without per-atom bindings

The central registry contains bibliographic records that may not be referenced by any per-atom file. These are still inserted into `references_registry` (the global bibliography should be complete). Only ref_ids that appear in per-atom files produce `atom_references` rows.

### 5.3 `confidence` empty string vs NULL

The `atom_references` schema uses `CHECK (confidence IN ('', 'low', 'medium', 'high'))` with a default of `''`. When `match_metadata.confidence` is absent, the script inserts `''`, not NULL. This matches the column default and CHECK constraint.

### 5.4 `match_type` values not in source mapping

If a future `match_type` value is encountered that is not in `MATCH_TYPE_TO_SOURCE`, the script falls back to `"manual"`. Conservative: unknown automated methods are treated as manual until the mapping is updated.

### 5.5 Atoms not yet in Supabase

If an atom FQDN from a per-atom file has not been migrated to Supabase (e.g., draft or deprecated atoms), the reference is skipped and logged as `skipped_no_atom`. These can be backfilled later when the atom is created.

### 5.6 Why a separate `references_registry` table?

The migration plan's `atom_references` table denormalizes bibliographic data (title, authors, year) into every binding row. This works well for serving, but creates maintenance overhead if a reference's metadata is corrected (e.g., fixing an author name requires updating every `atom_references` row for that ref_id).

The `references_registry` table:
- Provides a single source of truth for bibliographic records.
- Enables `ref_id`-based lookup for the `add_reference.py` workflow (which first upserts to registry, then creates per-atom bindings).
- Supports future features like BibTeX export, citation formatting, and DOI deduplication.
- Mirrors the existing file-based architecture (`registry.json` + per-atom `references.json`).

The denormalized fields in `atom_references` remain for query convenience and to match the migration plan's DDL. A periodic consistency check (Section 3.8) catches drift.

### 5.7 `ref_type` column naming

The JSON schema uses `type` as the field name. The SQL column is named `ref_type` to avoid conflict with the SQL reserved word `TYPE`. Application code should map between them.

---

## 6. Execution Checklist

1. [ ] Confirm Phase 1 is complete (`atoms` table populated with FQDNs).
2. [ ] Run DDL: create `references_registry` table and indexes.
3. [ ] Run DDL: create `atom_references` table, indexes, RLS policies, and trigger.
4. [ ] Run registry backfill in dry-run mode: `python3 scripts/backfill_references_registry.py --dry-run`
5. [ ] Run registry backfill for real: `python3 scripts/backfill_references_registry.py`
6. [ ] Verify registry row count (~85 entries).
7. [ ] Run atom-references backfill in dry-run mode: `python3 scripts/backfill_references.py --dry-run`
8. [ ] Review dry-run logs for `skipped_no_atom` and `skipped_no_registry` counts.
9. [ ] Run atom-references backfill for real: `python3 scripts/backfill_references.py`
10. [ ] Run validation queries from Section 3.
11. [ ] Run denormalization consistency check (Section 3.8).
12. [ ] Verify `get_atom_document()` RPC returns references.
13. [ ] Verify `catalog_atoms_index` materialized view shows non-zero `reference_count`.
14. [ ] Refresh materialized view: `REFRESH MATERIALIZED VIEW CONCURRENTLY public.catalog_atoms_index;`
