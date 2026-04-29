#!/usr/bin/env python3
"""Generate an ephemeral audit manifest from review bundles and run Supabase backfills.

Instead of reading a checked-in ``audit_manifest.json``, this script:

1. Discovers all ``sciona-atoms*`` repos under a workspace root.
2. For each repo, loads review bundles and merges them (in memory) against
   the repo's existing manifest entries (or an empty manifest if none exists).
3. Combines all per-repo entries into a single ephemeral manifest.
4. Writes the manifest to a temporary file.
5. Invokes the existing backfill functions (rollups, evidence) pointing at
   the temp file.
6. Cleans up the temp file.

Usage::

    python scripts/backfill_from_review_bundles.py --base-dir /Users/conrad/personal

    # Only rollups:
    python scripts/backfill_from_review_bundles.py --base-dir /Users/conrad/personal --only rollups

    # Only evidence:
    python scripts/backfill_from_review_bundles.py --base-dir /Users/conrad/personal --only evidence

    # Dry run (no Supabase writes):
    python scripts/backfill_from_review_bundles.py --base-dir /Users/conrad/personal --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PROVIDER_REPO_ORDER = (
    "sciona-atoms",
    "sciona-atoms-bio",
    "sciona-atoms-cs",
    "sciona-atoms-fintech",
    "sciona-atoms-ml",
    "sciona-atoms-physics",
    "sciona-atoms-robotics",
    "sciona-atoms-signal",
)

DEFAULT_BASE_DIR = Path("/Users/conrad/personal")


def _discover_provider_repos(base_dir: Path) -> list[tuple[str, Path]]:
    """Discover sciona-atoms* repos in provider order under *base_dir*."""

    def _order_key(name: str) -> tuple[int, str]:
        try:
            return (_PROVIDER_REPO_ORDER.index(name), name)
        except ValueError:
            return (len(_PROVIDER_REPO_ORDER), name)

    resolved = base_dir.expanduser().resolve()
    repos: list[tuple[str, Path]] = []
    for child in sorted(resolved.iterdir(), key=lambda p: _order_key(p.name)):
        if not child.is_dir():
            continue
        if child.name == "sciona-atoms" or child.name.startswith("sciona-atoms-"):
            repos.append((child.name, child.resolve()))
    return repos


def _ensure_import_paths(base_dir: Path) -> None:
    """Add src/ directories from provider repos to sys.path so imports resolve."""
    for _name, repo_root in _discover_provider_repos(base_dir):
        src_dir = repo_root / "src"
        if src_dir.is_dir() and str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))


def _build_ephemeral_manifest(base_dir: Path) -> dict[str, Any]:
    """Build a combined audit manifest from review bundles across all repos.

    For each repo:
    - Load the existing manifest entries (if the file exists) as a base.
    - Discover repo-local review bundles.
    - Merge bundles into the base entries in memory.
    - Collect all merged entries.

    Returns a manifest dict with ``{"metadata": ..., "atoms": [...]}``.
    """
    from sciona.atoms.audit_review_bundles import (
        _discover_repo_local_bundle_paths,
        load_review_bundle_entries,
        merge_audit_manifest_entries,
    )

    repos = _discover_provider_repos(base_dir)
    all_atoms: dict[str, dict[str, Any]] = {}  # keyed by atom_name for dedup
    per_repo_counts: dict[str, int] = {}
    source_repos: list[str] = []

    for repo_name, repo_root in repos:
        # Load existing manifest entries as the base (may be empty)
        manifest_path = repo_root / "data" / "audit_manifest.json"
        base_entries: list[dict[str, Any]] = []
        if manifest_path.is_file():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                atoms = payload.get("atoms", [])
                if isinstance(atoms, list):
                    base_entries = [e for e in atoms if isinstance(e, dict)]
            except Exception as exc:
                log.warning("Skipping manifest %s: %s", manifest_path, exc)

        # Discover and load review bundles scoped to this repo
        bundle_paths = _discover_repo_local_bundle_paths(repo_root)
        if not bundle_paths and not base_entries:
            continue

        review_entries = []
        for path in bundle_paths:
            try:
                review_entries.extend(load_review_bundle_entries(path))
            except Exception as exc:
                log.warning("Skipping bundle %s: %s", path, exc)

        review_entries.sort(key=lambda e: (e.atom_name, str(e.source_path)))

        # Merge review bundles into base manifest entries
        merged_atoms, skipped = merge_audit_manifest_entries(base_entries, review_entries)
        if skipped:
            log.info(
                "[%s] Skipped %d unresolved atoms: %s",
                repo_name,
                len(skipped),
                ", ".join(skipped[:10]),
            )

        count = 0
        for atom in merged_atoms:
            name = str(atom.get("atom_name") or "").strip()
            if not name:
                continue
            all_atoms[name] = atom
            count += 1

        per_repo_counts[repo_name] = count
        source_repos.append(repo_name)

    combined = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "scripts/backfill_from_review_bundles.py",
            "source_repos": source_repos,
            "total_atoms": len(all_atoms),
            "ephemeral": True,
        },
        "atoms": sorted(all_atoms.values(), key=lambda a: str(a.get("atom_name") or "")),
    }

    # Log summary
    log.info("--- Ephemeral manifest summary ---")
    for repo_name, count in per_repo_counts.items():
        log.info("  %s: %d atoms", repo_name, count)
    log.info("  TOTAL (deduplicated): %d atoms", len(combined["atoms"]))

    return combined


def _run_backfills(
    manifest_path: Path,
    *,
    only: str | None = None,
    batch_size: int = 50,
    runner_version: str = "backfill-v1",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the backfill functions against the given manifest path."""
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(url, key)

    results: dict[str, Any] = {}

    if only is None or only == "rollups":
        from scripts.backfill_audit_rollups import backfill_audit_rollups

        stats = backfill_audit_rollups(
            supabase,
            manifest_path=manifest_path,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        results["rollups"] = stats
        log.info("Rollup backfill: %s", stats)

    if only is None or only == "evidence":
        from scripts.backfill_audit_evidence import backfill_audit_evidence

        stats = backfill_audit_evidence(
            supabase,
            manifest_path=manifest_path,
            batch_size=batch_size,
            runner_version=runner_version,
            dry_run=dry_run,
        )
        results["evidence"] = stats
        log.info("Evidence backfill: %s", stats)

    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate ephemeral audit manifest from review bundles and run Supabase backfills.",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path(os.environ.get("SCIONA_WORKSPACE_ROOT", DEFAULT_BASE_DIR)),
        help="Workspace root containing sibling sciona-atoms* repos",
    )
    parser.add_argument(
        "--only",
        choices=["rollups", "evidence"],
        default=None,
        help="Run only one backfill type (default: both)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Upsert/insert batch size (default: 50)",
    )
    parser.add_argument(
        "--runner-version",
        default="backfill-v1",
        help="Runner version tag for evidence rows (default: backfill-v1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the manifest and rows without writing to Supabase",
    )
    parser.add_argument(
        "--dump-manifest",
        type=Path,
        default=None,
        help="Also write the ephemeral manifest to this path for debugging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    base_dir = args.base_dir.expanduser().resolve()
    if not base_dir.is_dir():
        log.error("Base directory does not exist: %s", base_dir)
        return 1

    # Ensure provider repo src/ dirs are importable
    _ensure_import_paths(base_dir)

    # Build the ephemeral manifest from review bundles
    log.info("Building ephemeral manifest from review bundles under %s ...", base_dir)
    manifest = _build_ephemeral_manifest(base_dir)

    if not manifest["atoms"]:
        log.warning("No atoms found in any review bundles. Nothing to backfill.")
        return 0

    # Optionally dump for debugging
    if args.dump_manifest:
        args.dump_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.dump_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        log.info("Ephemeral manifest written to %s", args.dump_manifest)

    # Write to a temp file and run backfills
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="ephemeral_audit_manifest_",
        delete=False,
        encoding="utf-8",
    )
    try:
        json.dump(manifest, tmp, indent=2, sort_keys=False)
        tmp.write("\n")
        tmp.close()
        tmp_path = Path(tmp.name)
        log.info("Ephemeral manifest written to temp file: %s", tmp_path)

        # Add sciona-infra/scripts to sys.path so backfill modules are importable
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        results = _run_backfills(
            tmp_path,
            only=args.only,
            batch_size=args.batch_size,
            runner_version=args.runner_version,
            dry_run=args.dry_run,
        )
        log.info("Backfill complete: %s", json.dumps(results, indent=2))
    finally:
        try:
            os.unlink(tmp.name)
            log.info("Cleaned up temp file: %s", tmp.name)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
