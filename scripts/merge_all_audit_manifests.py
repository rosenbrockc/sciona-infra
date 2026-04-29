#!/usr/bin/env python3
"""Merge per-repo audit manifests into a combined manifest for Supabase backfill.

Each provider repo (sciona-atoms, sciona-atoms-ml, sciona-atoms-bio, etc.)
maintains its own scoped audit_manifest.json containing only atoms that
belong to that repo. This script combines them all for Supabase seeding.

Usage:
    python merge_all_audit_manifests.py --base-dir /Users/conrad/personal \
        --output data/combined_audit_manifest.json

    # Dry run (prints summary to stderr, manifest to stdout):
    python merge_all_audit_manifests.py --base-dir /Users/conrad/personal
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def _discover_provider_repos(base_dir: Path) -> list[tuple[str, Path]]:
    """Discover sciona-atoms* repos in provider order under *base_dir*."""
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


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    atoms = payload.get("atoms", [])
    if not isinstance(atoms, list):
        raise ValueError(f"Expected atoms list in {path}")
    payload["atoms"] = [entry for entry in atoms if isinstance(entry, dict)]
    return payload


def merge_all_audit_manifests(
    base_dir: Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge per-repo audit manifests into a single combined manifest.

    Returns (combined_manifest, per_repo_counts).
    Later repos override earlier ones by atom_name.
    """
    repos = _discover_provider_repos(base_dir)
    merged_atoms: dict[str, dict[str, Any]] = {}
    per_repo_counts: dict[str, int] = {}
    source_repos: list[str] = []

    for repo_name, repo_root in repos:
        manifest_path = repo_root / "data" / "audit_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _load_manifest(manifest_path)
        atoms = manifest.get("atoms", [])
        count = 0
        for atom in atoms:
            name = str(atom.get("atom_name") or "").strip()
            if not name:
                continue
            merged_atoms[name] = atom
            count += 1
        per_repo_counts[repo_name] = count
        source_repos.append(repo_name)

    combined = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "scripts/merge_all_audit_manifests.py",
            "source_repos": source_repos,
            "total_atoms": len(merged_atoms),
        },
        "atoms": sorted(merged_atoms.values(), key=lambda a: str(a.get("atom_name") or "")),
    }
    return combined, per_repo_counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge per-repo audit manifests into a combined manifest for Supabase backfill."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        required=True,
        help="Workspace root containing sibling sciona-atoms* repos",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for combined manifest (default: stdout)",
    )
    args = parser.parse_args(argv)

    combined, per_repo_counts = merge_all_audit_manifests(args.base_dir)

    # Print summary to stderr
    print("--- Audit manifest merge summary ---", file=sys.stderr)
    for repo_name, count in per_repo_counts.items():
        print(f"  {repo_name}: {count} atoms", file=sys.stderr)
    print(f"  TOTAL (deduplicated): {len(combined['atoms'])} atoms", file=sys.stderr)

    output_text = json.dumps(combined, indent=2, sort_keys=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
        print(f"  Written to: {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
