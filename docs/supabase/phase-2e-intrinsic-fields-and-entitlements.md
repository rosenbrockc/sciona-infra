# Phase 2E: Intrinsic Fields, Entitlements & Contribution Events

## Overview

Phase 2E populates two materialized columns that the RLS policies depend on at
runtime, creates the contribution event log, and seeds entitlement grants from
existing data. This is the phase that makes the access-control model operational.

| Deliverable | Target column/table | Computation source |
|---|---|---|
| Publication completeness backfill | `atoms.is_publishable` | 5-pillar EXISTS check across `atom_io_specs`, `atom_parameters`, `atom_descriptions`, `atom_audit_rollups`, `atom_references` |
| Contribution event log | `contribution_events` (new table) | Git history, `atom_authors`, bounty wins |
| Entitlement grant seeding | `user_entitlement_grants` | Contribution events, role assignments, manual seeds |
| Effective tier backfill | `users.effective_tier` | `user_effective_entitlement()` recomputation across all grant sources |

---

## CRITICAL: Dependency on Phases 2A-2D

> **This phase CANNOT run until Phases 2A, 2B, 2C, and 2D are complete.**

Despite being labeled "2E" and appearing parallel in the phase numbering, every
step in this phase reads data that the earlier phases populate:

| Pillar | Source phase | Table read |
|---|---|---|
| IO specifications | Phase 2A | `atom_io_specs` |
| Parameters | Phase 2A | `atom_parameters` |
| Dejargonized descriptions | Phase 2A | `atom_descriptions` (kind='dejargonized') |
| Audit rollups | Phase 2B | `atom_audit_rollups` |
| Academic references | Phase 2C | `atom_references` |
| Atom authorship | Phase 1 | `atom_authors` |
| Bounty wins | Phase 1 | `submissions` (is_winner=true) |

If any of 2A-2D are incomplete, the `is_publishable` computation will produce
false negatives -- atoms that have all five pillars populated but whose data
has not yet been migrated will be incorrectly marked unpublishable. The
contribution event generation also depends on `atom_authors` (Phase 1) being
fully populated.

**Execution order**: 1 -> 2A/2B/2C/2D (parallel) -> 2E (this phase).

---

## Step 1: `contribution_events` Table

> **REFERENCE ONLY** — This table is already created in **Phase 0** (with a
> `UNIQUE (user_id, event_kind, entity_id)` constraint for idempotent backfill).
> Do NOT execute the DDL below. **Skip to Step 2 when executing this phase.**

This table is an append-only log of contribution-worthy actions. It serves as
the source of truth for computing contribution-based entitlement grants.

### DDL (reference)

```sql
-- ============================================================
-- CONTRIBUTION EVENTS
-- ============================================================

CREATE TABLE public.contribution_events (
    event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    event_kind       TEXT NOT NULL
                     CHECK (event_kind IN (
                         'atom_authorship',          -- authored an approved atom
                         'atom_documentation',       -- contributed io_specs/parameters/descriptions
                         'atom_uncertainty',          -- contributed uncertainty estimates
                         'atom_reference',            -- contributed academic references
                         'atom_update',               -- updated an existing atom version
                         'cdg_submission',            -- submitted a CDG that uses atoms
                         'bounty_win'                 -- won a bounty
                     )),
    -- What entity this contribution relates to
    entity_kind      TEXT NOT NULL DEFAULT 'atom'
                     CHECK (entity_kind IN ('atom', 'bounty', 'cdg')),
    entity_id        UUID,                          -- atom_id, bounty_id, etc.
    entity_fqdn      TEXT NOT NULL DEFAULT '',       -- human-readable reference
    -- When the contribution was approved/finalized
    approved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Provenance
    source           TEXT NOT NULL DEFAULT 'backfill'
                     CHECK (source IN ('backfill', 'git_history', 'api', 'admin')),
    source_ref       TEXT NOT NULL DEFAULT '',       -- commit SHA, PR URL, etc.
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contribution_events_user ON public.contribution_events (user_id);
CREATE INDEX idx_contribution_events_kind ON public.contribution_events (event_kind);
CREATE INDEX idx_contribution_events_approved ON public.contribution_events (approved_at);
CREATE INDEX idx_contribution_events_entity ON public.contribution_events (entity_kind, entity_id);

ALTER TABLE public.contribution_events ENABLE ROW LEVEL SECURITY;

-- Users can see their own contribution events
CREATE POLICY contribution_events_select_own ON public.contribution_events
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- Service role handles inserts (backfill + API writes)
```

---

## Step 2: Backfill `atoms.is_publishable`

### 2.1 Prerequisite: Verify Pillar Data Completeness

Before running the backfill, confirm that 2A-2D have populated the expected data.

```sql
-- Pre-flight check: pillar data must exist before is_publishable makes sense

-- Pillar 1: IO specs
SELECT count(DISTINCT atom_id) AS atoms_with_io_specs
FROM public.atom_io_specs;
-- Expected: ~505 (all atoms should have at least one io_spec)

-- Pillar 2: Parameters
SELECT count(DISTINCT atom_id) AS atoms_with_parameters
FROM public.atom_parameters;
-- Expected: ~505

-- Pillar 3: Dejargonized descriptions (jargon_score < 0.4)
SELECT count(DISTINCT atom_id) AS atoms_with_dejargonized
FROM public.atom_descriptions
WHERE kind = 'dejargonized'
  AND language = 'en'
  AND jargon_score < 0.4;
-- Expected: varies based on LLM description quality; flag any < 400

-- Pillar 4: Audit rollups
SELECT count(*) AS atoms_with_rollups
FROM public.atom_audit_rollups;
-- Expected: 505

-- Pillar 5: References
SELECT count(DISTINCT atom_id) AS atoms_with_references
FROM public.atom_references;
-- Expected: varies; some atoms may legitimately lack references

-- Summary: how many atoms satisfy ALL five pillars?
SELECT count(*) AS fully_publishable
FROM public.atoms a
WHERE EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = a.atom_id)
  AND EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id)
  AND EXISTS (
      SELECT 1 FROM public.atom_descriptions d
      WHERE d.atom_id = a.atom_id
        AND d.kind = 'dejargonized'
        AND d.language = 'en'
        AND d.jargon_score < 0.4
  )
  AND EXISTS (SELECT 1 FROM public.atom_audit_rollups ar WHERE ar.atom_id = a.atom_id)
  AND EXISTS (SELECT 1 FROM public.atom_references r WHERE r.atom_id = a.atom_id);
-- Record this number before proceeding. If it is 0, stop and investigate.
```

### 2.2 Create Publication Completeness Function and Triggers

These are defined in Section 2.3.1 of the migration plan. If not already created
by an earlier phase, create them now.

```sql
-- ============================================================
-- PUBLICATION COMPLETENESS GATE
-- ============================================================

CREATE OR REPLACE FUNCTION public.atom_is_publishable(check_atom_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT
        EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = check_atom_id)
        AND EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = check_atom_id)
        AND EXISTS (
            SELECT 1
            FROM public.atom_descriptions d
            WHERE d.atom_id = check_atom_id
              AND d.kind = 'dejargonized'
              AND d.language = 'en'
              AND d.jargon_score < 0.4
        )
        AND EXISTS (SELECT 1 FROM public.atom_audit_rollups ar WHERE ar.atom_id = check_atom_id)
        AND EXISTS (SELECT 1 FROM public.atom_references r WHERE r.atom_id = check_atom_id);
$$;

-- Trigger to keep atoms.is_publishable in sync.
-- Fires on INSERT/UPDATE/DELETE to any of the five pillar tables.
CREATE OR REPLACE FUNCTION public.refresh_atom_publishable()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
DECLARE
    target_atom_id UUID;
BEGIN
    target_atom_id := COALESCE(NEW.atom_id, OLD.atom_id);
    UPDATE public.atoms
       SET is_publishable = public.atom_is_publishable(target_atom_id),
           updated_at = now()
     WHERE atom_id = target_atom_id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_publishable_io_specs
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_io_specs
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_parameters
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_parameters
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_descriptions
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_descriptions
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_rollups
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_audit_rollups
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();

CREATE TRIGGER trg_publishable_references
    AFTER INSERT OR UPDATE OR DELETE ON public.atom_references
    FOR EACH ROW EXECUTE FUNCTION public.refresh_atom_publishable();
```

### 2.3 Bulk Backfill SQL

After confirming pillar data is present, run a single bulk UPDATE to set
`is_publishable` for all atoms. This is faster than relying on triggers
(which fire per-row during backfill inserts).

```sql
-- ============================================================
-- BULK BACKFILL: atoms.is_publishable
-- ============================================================

-- Disable triggers temporarily to avoid per-row trigger overhead during bulk update
ALTER TABLE public.atom_io_specs DISABLE TRIGGER trg_publishable_io_specs;
ALTER TABLE public.atom_parameters DISABLE TRIGGER trg_publishable_parameters;
ALTER TABLE public.atom_descriptions DISABLE TRIGGER trg_publishable_descriptions;
ALTER TABLE public.atom_audit_rollups DISABLE TRIGGER trg_publishable_rollups;
ALTER TABLE public.atom_references DISABLE TRIGGER trg_publishable_references;

-- Set is_publishable for every atom using the canonical function
UPDATE public.atoms
   SET is_publishable = public.atom_is_publishable(atom_id),
       updated_at = now();

-- Re-enable triggers
ALTER TABLE public.atom_io_specs ENABLE TRIGGER trg_publishable_io_specs;
ALTER TABLE public.atom_parameters ENABLE TRIGGER trg_publishable_parameters;
ALTER TABLE public.atom_descriptions ENABLE TRIGGER trg_publishable_descriptions;
ALTER TABLE public.atom_audit_rollups ENABLE TRIGGER trg_publishable_rollups;
ALTER TABLE public.atom_references ENABLE TRIGGER trg_publishable_references;
```

### 2.4 Python Script: Publication Completeness Diagnostic

This script produces a per-atom report showing which pillars are missing,
useful for triaging atoms that fail the publication gate.

```python
#!/usr/bin/env python3
"""
scripts/diagnose_publication_completeness.py

Reports which of the 5 publication pillars each atom is missing.
Run AFTER Phases 2A-2D are complete and is_publishable has been backfilled.

Requires: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY env vars.
"""
import os
import sys

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

PILLAR_QUERIES = {
    "io_specs": """
        SELECT DISTINCT atom_id FROM public.atom_io_specs
    """,
    "parameters": """
        SELECT DISTINCT atom_id FROM public.atom_parameters
    """,
    "dejargonized_description": """
        SELECT DISTINCT atom_id FROM public.atom_descriptions
        WHERE kind = 'dejargonized' AND language = 'en' AND jargon_score < 0.4
    """,
    "audit_rollups": """
        SELECT atom_id FROM public.atom_audit_rollups
    """,
    "references": """
        SELECT DISTINCT atom_id FROM public.atom_references
    """,
}


def main():
    # Fetch all atoms
    resp = supabase.table("atoms").select("atom_id, fqdn, is_publishable").execute()
    atoms = {a["atom_id"]: a for a in resp.data}

    # Fetch pillar coverage sets
    pillar_sets = {}
    for pillar_name, query in PILLAR_QUERIES.items():
        result = supabase.rpc("raw_sql", {"query": query}).execute()
        # Alternative: use table queries if RPC not available
        pillar_sets[pillar_name] = set()
        # Implementation note: adapt to actual Supabase query pattern
        # This sketch assumes an RPC that returns atom_id rows

    # For practical implementation, use table-level queries:
    pillar_sets = {}

    resp = supabase.table("atom_io_specs").select("atom_id").execute()
    pillar_sets["io_specs"] = {r["atom_id"] for r in resp.data}

    resp = supabase.table("atom_parameters").select("atom_id").execute()
    pillar_sets["parameters"] = {r["atom_id"] for r in resp.data}

    resp = (
        supabase.table("atom_descriptions")
        .select("atom_id")
        .eq("kind", "dejargonized")
        .eq("language", "en")
        .lt("jargon_score", 0.4)
        .execute()
    )
    pillar_sets["dejargonized_description"] = {r["atom_id"] for r in resp.data}

    resp = supabase.table("atom_audit_rollups").select("atom_id").execute()
    pillar_sets["audit_rollups"] = {r["atom_id"] for r in resp.data}

    resp = supabase.table("atom_references").select("atom_id").execute()
    pillar_sets["references"] = {r["atom_id"] for r in resp.data}

    # Report
    publishable_count = 0
    missing_report = []

    for atom_id, atom in sorted(atoms.items(), key=lambda x: x[1]["fqdn"]):
        missing = []
        for pillar_name, id_set in pillar_sets.items():
            if atom_id not in id_set:
                missing.append(pillar_name)

        if not missing:
            publishable_count += 1
        else:
            missing_report.append((atom["fqdn"], missing))

    print(f"Total atoms: {len(atoms)}")
    print(f"Publishable (all 5 pillars): {publishable_count}")
    print(f"Unpublishable: {len(missing_report)}")
    print()

    if missing_report:
        print("--- Missing pillars by atom ---")
        for fqdn, missing in missing_report:
            print(f"  {fqdn}: {', '.join(missing)}")

    # Verify against materialized column
    mismatch_count = sum(
        1 for a in atoms.values()
        if a["is_publishable"] != (a["atom_id"] in pillar_sets["io_specs"]
                                    and a["atom_id"] in pillar_sets["parameters"]
                                    and a["atom_id"] in pillar_sets["dejargonized_description"]
                                    and a["atom_id"] in pillar_sets["audit_rollups"]
                                    and a["atom_id"] in pillar_sets["references"])
    )
    if mismatch_count:
        print(f"\nWARNING: {mismatch_count} atoms have is_publishable out of sync!")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Step 3: Generate Contribution Events from Existing Data

Three data sources produce contribution events during backfill:

1. **`atom_authors`** -- every author of an approved atom gets an `atom_authorship` event
2. **`submissions` with `is_winner = true`** -- bounty winners get a `bounty_win` event
3. **Git history** (optional, for enrichment) -- commit authorship on ageo-atoms

### 3.1 Contribution Events from Atom Authorship

```sql
-- ============================================================
-- BACKFILL: contribution_events from atom_authors
-- ============================================================

INSERT INTO public.contribution_events
    (user_id, event_kind, entity_kind, entity_id, entity_fqdn, approved_at, source, source_ref)
SELECT
    aa.user_id,
    'atom_authorship',
    'atom',
    aa.atom_id,
    a.fqdn,
    a.created_at,           -- use atom creation time as approval time
    'backfill',
    ''
FROM public.atom_authors aa
JOIN public.atoms a ON a.atom_id = aa.atom_id
WHERE a.status = 'approved'
ON CONFLICT DO NOTHING;     -- safe for re-runs
```

### 3.2 Contribution Events from Bounty Wins

```sql
-- ============================================================
-- BACKFILL: contribution_events from bounty wins
-- ============================================================

INSERT INTO public.contribution_events
    (user_id, event_kind, entity_kind, entity_id, entity_fqdn, approved_at, source, source_ref)
SELECT
    s.architect_id,
    'bounty_win',
    'bounty',
    s.bounty_id,
    b.title,
    COALESCE(s.verified_at, s.submitted_at),  -- use verification time as approval
    'backfill',
    s.submission_id::text
FROM public.submissions s
JOIN public.bounties b ON b.bounty_id = s.bounty_id
WHERE s.is_winner = TRUE
  AND b.status IN ('verified', 'settled')
ON CONFLICT DO NOTHING;
```

### 3.3 Contribution Events from Git History (Python Script)

This script parses git log from the ageo-atoms repository to create contribution
events for users whose commits introduced or substantially modified atoms.

```python
#!/usr/bin/env python3
"""
scripts/backfill_contribution_events_git.py

Generates contribution_events from git history in the ageo-atoms repo.
Maps git author emails to user_ids via the users table.

Requires:
  - SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY env vars
  - AGEO_ATOMS_REPO_PATH env var (path to cloned ageo-atoms repo)
"""
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REPO_PATH = os.environ.get("AGEO_ATOMS_REPO_PATH", "../ageo-atoms")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_git_log():
    """Extract commits that touch atom source files."""
    result = subprocess.run(
        [
            "git", "log",
            "--format=%H|%ae|%aI",
            "--diff-filter=AM",           # Added or Modified
            "--name-only",
            "--", "ageoa/*/atoms.py",     # atom source files
        ],
        capture_output=True,
        text=True,
        cwd=REPO_PATH,
    )
    if result.returncode != 0:
        print(f"git log failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def parse_git_log(raw_log: str):
    """Parse git log into (commit_sha, email, date, files) tuples."""
    entries = []
    current = None
    for line in raw_log.strip().split("\n"):
        if "|" in line and line.count("|") == 2:
            if current:
                entries.append(current)
            sha, email, date_str = line.split("|", 2)
            current = {
                "sha": sha,
                "email": email,
                "date": date_str,
                "files": [],
            }
        elif line.strip() and current:
            current["files"].append(line.strip())
    if current:
        entries.append(current)
    return entries


def build_email_to_user_map():
    """Map email addresses to user_ids."""
    resp = supabase.table("users").select("user_id, email, github_login").execute()
    email_map = {}
    for u in resp.data:
        if u["email"]:
            email_map[u["email"].lower()] = u["user_id"]
    return email_map


def derive_atom_fqdn_from_path(file_path: str):
    """
    Derive atom family from file path like 'ageoa/advancedvi/atoms.py'.
    Returns the family namespace prefix for matching against atoms.fqdn.
    """
    parts = file_path.split("/")
    if len(parts) >= 2:
        return parts[1]  # family name
    return None


def main():
    raw_log = get_git_log()
    entries = parse_git_log(raw_log)
    email_map = build_email_to_user_map()

    # Load atom fqdn -> atom_id mapping
    resp = supabase.table("atoms").select("atom_id, fqdn, created_at").execute()
    fqdn_map = {a["fqdn"]: a for a in resp.data}

    # Group: for each (user, atom_family), find earliest commit
    user_family_events = defaultdict(lambda: {"sha": None, "date": None})

    for entry in entries:
        user_id = email_map.get(entry["email"].lower())
        if not user_id:
            continue
        for file_path in entry["files"]:
            family = derive_atom_fqdn_from_path(file_path)
            if not family:
                continue
            key = (user_id, family)
            existing = user_family_events[key]
            if existing["date"] is None or entry["date"] < existing["date"]:
                user_family_events[key] = {
                    "sha": entry["sha"],
                    "date": entry["date"],
                }

    # Insert contribution events
    inserted = 0
    skipped = 0
    for (user_id, family), event_data in user_family_events.items():
        # Find matching atoms for this family
        matching_atoms = [
            a for fqdn, a in fqdn_map.items()
            if f".{family}." in fqdn or fqdn.endswith(f".{family}")
        ]
        if not matching_atoms:
            skipped += 1
            continue

        for atom in matching_atoms:
            try:
                supabase.table("contribution_events").insert({
                    "user_id": user_id,
                    "event_kind": "atom_authorship",
                    "entity_kind": "atom",
                    "entity_id": atom["atom_id"],
                    "entity_fqdn": atom["fqdn"],
                    "approved_at": event_data["date"],
                    "source": "git_history",
                    "source_ref": event_data["sha"],
                }).execute()
                inserted += 1
            except Exception as e:
                # Likely duplicate; skip
                skipped += 1

    print(f"Git contribution events inserted: {inserted}")
    print(f"Skipped: {skipped}")


if __name__ == "__main__":
    main()
```

---

## Step 4: Seed Entitlement Grants from Contribution Events

Contribution events are translated into `user_entitlement_grants` rows. Per the
migration plan (Section 2.1.1), a contribution grant gives `early_access` for
**12 months** from the approval date.

### 4.1 SQL: Generate Grants from Contribution Events

```sql
-- ============================================================
-- SEED: user_entitlement_grants from contribution_events
-- ============================================================

-- Each user with at least one contribution event gets an early_access grant
-- lasting 12 months from their most recent contribution.

INSERT INTO public.user_entitlement_grants
    (user_id, source_kind, entitlement_tier, source_ref, granted_at, expires_at)
SELECT
    ce.user_id,
    'contribution',
    'early_access',
    'backfill:contribution_events',
    max_approved,
    max_approved + INTERVAL '12 months'
FROM (
    SELECT
        user_id,
        MAX(approved_at) AS max_approved
    FROM public.contribution_events
    GROUP BY user_id
) ce
-- Do not create grants that have already expired
WHERE ce.max_approved + INTERVAL '12 months' > now()
-- Avoid duplicates if re-running
AND NOT EXISTS (
    SELECT 1 FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = ce.user_id
      AND ueg.source_kind = 'contribution'
      AND ueg.source_ref = 'backfill:contribution_events'
);
```

### 4.2 SQL: Seed Grants from Role Assignments

If role assignments were seeded in a prior phase (e.g., admin users assigned the
`Administrator` role), the `refresh_user_effective_tier` trigger should have
already created the correct `effective_tier`. However, we also want explicit
grant rows for auditability.

```sql
-- ============================================================
-- SEED: user_entitlement_grants from role assignments
-- ============================================================

INSERT INTO public.user_entitlement_grants
    (user_id, source_kind, entitlement_tier, source_ref, granted_at)
SELECT
    ura.user_id,
    'role',
    r.grants_tier,
    'role:' || ura.role_name,
    ura.granted_at
FROM public.user_role_assignments ura
JOIN public.roles r ON r.role_name = ura.role_name
WHERE (ura.expires_at IS NULL OR ura.expires_at > now())
AND NOT EXISTS (
    SELECT 1 FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = ura.user_id
      AND ueg.source_kind = 'role'
      AND ueg.source_ref = 'role:' || ura.role_name
);
```

---

## Step 5: Backfill `users.effective_tier`

After all entitlement grants are seeded, recompute the materialized
`effective_tier` column for every user.

### 5.1 Bulk UPDATE

```sql
-- ============================================================
-- BULK BACKFILL: users.effective_tier
-- ============================================================

-- Temporarily disable the refresh trigger to avoid recursive fires
ALTER TABLE public.user_entitlement_grants DISABLE TRIGGER trg_effective_tier_grants;
ALTER TABLE public.user_role_assignments DISABLE TRIGGER trg_effective_tier_roles;
ALTER TABLE public.organization_memberships DISABLE TRIGGER trg_effective_tier_org_memberships;
ALTER TABLE public.user_memberships DISABLE TRIGGER trg_effective_tier_memberships;

-- Recompute effective_tier for every user
UPDATE public.users
   SET effective_tier = public.user_effective_entitlement(user_id),
       updated_at = now();

-- Re-enable triggers
ALTER TABLE public.user_entitlement_grants ENABLE TRIGGER trg_effective_tier_grants;
ALTER TABLE public.user_role_assignments ENABLE TRIGGER trg_effective_tier_roles;
ALTER TABLE public.organization_memberships ENABLE TRIGGER trg_effective_tier_org_memberships;
ALTER TABLE public.user_memberships ENABLE TRIGGER trg_effective_tier_memberships;
```

### 5.2 Entitlement Tier Model Reference

The RLS policies rely on these tiers, ordered from least to most privileged:

| Tier | Description | Access |
|---|---|---|
| `general` | Default for all authenticated users | Atoms with `visibility_tier = 'general'` and `is_publishable = TRUE` |
| `early_access` | Contributors, paid members, org members | Above + atoms with `visibility_tier = 'early_access'` |
| `internal` | Staff, maintainers, admins | Above + atoms with `visibility_tier = 'internal'` + unpublishable atoms |

Anonymous (`anon` role) users see only the `catalog_public` view: approved,
general-tier, publishable atoms with only fqdn/description/domain_tags exposed.

Grant sources that produce each tier:

| Source | Tier granted | Duration |
|---|---|---|
| Approved contribution (atom, docs, uncertainty, reference, CDG) | `early_access` | 12 months from approval |
| Bounty win | `early_access` | 12 months from verification |
| Paid membership (active/trialing) | `early_access` | Until subscription ends |
| Organization membership (active org) | Organization's `entitlement_tier` | Until membership ends |
| Role: Free Member | `general` | Until removed |
| Role: Board Member, Org Member, Paid Member | `early_access` | Until removed |
| Role: Administrator, Founder, Maintainer, Foundation Staff | `internal` | Until removed |

The `user_effective_entitlement()` function takes the MAX across all active
grants (tier ordering: `internal` > `early_access` > `general`).

---

## Step 6: Validation

### 6.1 `is_publishable` Validation

```sql
-- Confirm is_publishable matches the canonical function for all atoms
SELECT count(*) AS mismatches
FROM public.atoms a
WHERE a.is_publishable != public.atom_is_publishable(a.atom_id);
-- Expected: 0

-- Distribution of publishable vs unpublishable
SELECT is_publishable, count(*) FROM public.atoms GROUP BY is_publishable;

-- Breakdown of missing pillars for unpublishable atoms
SELECT
    'io_specs' AS pillar,
    count(*) AS atoms_missing
FROM public.atoms a
WHERE NOT EXISTS (SELECT 1 FROM public.atom_io_specs ios WHERE ios.atom_id = a.atom_id)
UNION ALL
SELECT 'parameters', count(*)
FROM public.atoms a
WHERE NOT EXISTS (SELECT 1 FROM public.atom_parameters p WHERE p.atom_id = a.atom_id)
UNION ALL
SELECT 'dejargonized_desc', count(*)
FROM public.atoms a
WHERE NOT EXISTS (
    SELECT 1 FROM public.atom_descriptions d
    WHERE d.atom_id = a.atom_id AND d.kind = 'dejargonized'
      AND d.language = 'en' AND d.jargon_score < 0.4
)
UNION ALL
SELECT 'audit_rollups', count(*)
FROM public.atoms a
WHERE NOT EXISTS (SELECT 1 FROM public.atom_audit_rollups ar WHERE ar.atom_id = a.atom_id)
UNION ALL
SELECT 'references', count(*)
FROM public.atoms a
WHERE NOT EXISTS (SELECT 1 FROM public.atom_references r WHERE r.atom_id = a.atom_id);
```

### 6.2 Contribution Events Validation

```sql
-- Total contribution events by kind
SELECT event_kind, count(*) FROM public.contribution_events GROUP BY event_kind;
-- Expected: atom_authorship >= number of (user, atom) pairs in atom_authors
--           bounty_win >= number of winning submissions

-- Users with contribution events
SELECT count(DISTINCT user_id) AS contributing_users
FROM public.contribution_events;

-- Cross-check: every atom_author should have at least one contribution event
SELECT count(*) AS authors_without_events
FROM (
    SELECT DISTINCT aa.user_id
    FROM public.atom_authors aa
    JOIN public.atoms a ON a.atom_id = aa.atom_id
    WHERE a.status = 'approved'
) authors
WHERE NOT EXISTS (
    SELECT 1 FROM public.contribution_events ce
    WHERE ce.user_id = authors.user_id
      AND ce.event_kind = 'atom_authorship'
);
-- Expected: 0
```

### 6.3 Entitlement Grants Validation

```sql
-- Grants by source_kind
SELECT source_kind, count(*) FROM public.user_entitlement_grants GROUP BY source_kind;

-- Users with active contribution-based early_access grants
SELECT count(*) AS users_with_contribution_access
FROM public.user_entitlement_grants
WHERE source_kind = 'contribution'
  AND entitlement_tier = 'early_access'
  AND (expires_at IS NULL OR expires_at > now());

-- Users with expired contribution grants (contributed > 12 months ago)
SELECT count(*) AS expired_contribution_grants
FROM public.user_entitlement_grants
WHERE source_kind = 'contribution'
  AND expires_at IS NOT NULL
  AND expires_at <= now();
```

### 6.4 Effective Tier Validation

```sql
-- Distribution of effective_tier across all users
SELECT effective_tier, count(*) FROM public.users GROUP BY effective_tier;

-- Confirm effective_tier matches the canonical function for all users
SELECT count(*) AS mismatches
FROM public.users u
WHERE u.effective_tier != public.user_effective_entitlement(u.user_id);
-- Expected: 0

-- Specific tier assignment checks:

-- All users with 'Administrator' role should have effective_tier = 'internal'
SELECT u.user_id, u.github_login, u.effective_tier
FROM public.users u
JOIN public.user_role_assignments ura ON ura.user_id = u.user_id
WHERE ura.role_name = 'Administrator'
  AND u.effective_tier != 'internal';
-- Expected: 0 rows

-- All users with only contribution grants should have effective_tier = 'early_access'
-- (unless grant has expired)
SELECT u.user_id, u.github_login, u.effective_tier
FROM public.users u
WHERE EXISTS (
    SELECT 1 FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = u.user_id
      AND ueg.source_kind = 'contribution'
      AND ueg.entitlement_tier = 'early_access'
      AND (ueg.expires_at IS NULL OR ueg.expires_at > now())
)
AND NOT EXISTS (
    SELECT 1 FROM public.user_role_assignments ura
    WHERE ura.user_id = u.user_id
)
AND u.effective_tier != 'early_access';
-- Expected: 0 rows

-- Users with no grants at all should have effective_tier = 'general'
SELECT u.user_id, u.github_login, u.effective_tier
FROM public.users u
WHERE NOT EXISTS (
    SELECT 1 FROM public.user_entitlement_grants ueg
    WHERE ueg.user_id = u.user_id
      AND (ueg.expires_at IS NULL OR ueg.expires_at > now())
)
AND NOT EXISTS (
    SELECT 1 FROM public.user_role_assignments ura
    WHERE ura.user_id = u.user_id
      AND (ura.expires_at IS NULL OR ura.expires_at > now())
)
AND NOT EXISTS (
    SELECT 1 FROM public.organization_memberships om
    JOIN public.organizations o ON o.organization_id = om.organization_id
    WHERE om.user_id = u.user_id
      AND o.membership_status = 'active'
      AND (om.ends_at IS NULL OR om.ends_at > now())
)
AND NOT EXISTS (
    SELECT 1 FROM public.user_memberships um
    WHERE um.user_id = u.user_id
      AND um.status IN ('active', 'trialing')
      AND (um.ends_at IS NULL OR um.ends_at > now())
)
AND u.effective_tier != 'general';
-- Expected: 0 rows
```

### 6.5 End-to-End RLS Smoke Test

After all backfills complete, verify the access model works end-to-end.

```sql
-- Simulate anonymous access: should only see publishable general atoms
SET ROLE anon;
SELECT count(*) FROM public.atoms;
-- Expected: count of atoms WHERE is_publishable AND visibility_tier = 'general'
RESET ROLE;

-- Simulate authenticated general user (no grants)
-- Use set_config to simulate auth.uid() for a known general-tier user
-- (actual test requires a Supabase client with a real JWT)

-- Simulate early_access user: should see general + early_access publishable atoms
-- Simulate internal user: should see all atoms regardless of is_publishable
```

---

## Rollback Procedure

Rollback in reverse order. Steps are independent of Phases 2A-2D data (those
tables are untouched), but this phase's changes depend on that data being present.

### Step 1: Reset `users.effective_tier` to default

```sql
UPDATE public.users SET effective_tier = 'general', updated_at = now();
```

### Step 2: Delete backfilled entitlement grants

```sql
DELETE FROM public.user_entitlement_grants
WHERE source_ref LIKE 'backfill:%'
   OR source_ref LIKE 'role:%';
```

### Step 3: Delete contribution events

```sql
DELETE FROM public.contribution_events
WHERE source IN ('backfill', 'git_history');
```

### Step 4: Drop contribution_events table

```sql
DROP TABLE IF EXISTS public.contribution_events;
```

### Step 5: Reset `atoms.is_publishable` to default

```sql
UPDATE public.atoms SET is_publishable = FALSE, updated_at = now();
```

### Step 6: Drop publication completeness function and triggers

```sql
DROP TRIGGER IF EXISTS trg_publishable_io_specs ON public.atom_io_specs;
DROP TRIGGER IF EXISTS trg_publishable_parameters ON public.atom_parameters;
DROP TRIGGER IF EXISTS trg_publishable_descriptions ON public.atom_descriptions;
DROP TRIGGER IF EXISTS trg_publishable_rollups ON public.atom_audit_rollups;
DROP TRIGGER IF EXISTS trg_publishable_references ON public.atom_references;

DROP FUNCTION IF EXISTS public.refresh_atom_publishable();
DROP FUNCTION IF EXISTS public.atom_is_publishable(UUID);
```

---

## Execution Checklist

- [ ] **Prerequisite**: Confirm Phase 1 is complete (users, atoms, atom_authors, submissions, bounties)
- [ ] **Prerequisite**: Confirm Phase 2A is complete (atom_io_specs, atom_parameters, atom_descriptions populated)
- [ ] **Prerequisite**: Confirm Phase 2B is complete (atom_audit_rollups populated)
- [ ] **Prerequisite**: Confirm Phase 2C is complete (atom_references populated)
- [ ] **Prerequisite**: Confirm Phase 2D is complete (uncertainty estimates, verification matches populated)
- [ ] Run Step 1 DDL: create `contribution_events` table
- [ ] Run Step 2.1 pre-flight queries: verify pillar data completeness
- [ ] Run Step 2.2 DDL: create `atom_is_publishable()` function and triggers
- [ ] Run Step 2.3 SQL: bulk backfill `atoms.is_publishable`
- [ ] Run Step 2.4 script: `diagnose_publication_completeness.py` to triage failures
- [ ] Run Step 3.1 SQL: backfill contribution events from atom_authors
- [ ] Run Step 3.2 SQL: backfill contribution events from bounty wins
- [ ] Run Step 3.3 script: `backfill_contribution_events_git.py` (optional enrichment)
- [ ] Run Step 4.1 SQL: seed entitlement grants from contribution events
- [ ] Run Step 4.2 SQL: seed entitlement grants from role assignments
- [ ] Run Step 5.1 SQL: bulk backfill `users.effective_tier`
- [ ] Run Step 6.1 validation: `is_publishable` correctness
- [ ] Run Step 6.2 validation: contribution events completeness
- [ ] Run Step 6.3 validation: entitlement grants
- [ ] Run Step 6.4 validation: effective tier correctness (0 mismatches)
- [ ] Run Step 6.5 validation: end-to-end RLS smoke test
