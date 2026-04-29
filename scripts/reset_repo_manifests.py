#!/usr/bin/env python3
"""Reset audit manifests so each repo only contains atoms from its own namespace.

Scans each repo's src/sciona/atoms/ directory to discover which namespaces it
owns, then filters the repo's audit_manifest.json to keep only matching atoms.

Usage:
    python reset_repo_manifests.py --dry-run   # report only, no writes
    python reset_repo_manifests.py              # apply changes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/conrad/personal")

# Repos and their root paths.  The base repo (sciona-atoms) is special:
# it owns all top-level namespaces that are NOT claimed by a sibling repo.
REPOS: dict[str, Path] = {
    "sciona-atoms": BASE / "sciona-atoms",
    "sciona-atoms-ml": BASE / "sciona-atoms-ml",
    "sciona-atoms-bio": BASE / "sciona-atoms-bio",
    "sciona-atoms-dl": BASE / "sciona-atoms-dl",
    "sciona-atoms-cs": BASE / "sciona-atoms-cs",
    "sciona-atoms-fintech": BASE / "sciona-atoms-fintech",
    "sciona-atoms-physics": BASE / "sciona-atoms-physics",
    "sciona-atoms-robotics": BASE / "sciona-atoms-robotics",
    "sciona-atoms-signal": BASE / "sciona-atoms-signal",
    "sciona-atoms-geo": BASE / "sciona-atoms-geo",
}


def discover_owned_namespaces(repo_path: Path) -> set[str]:
    """Return the set of 3rd-level namespace segments this repo owns.

    For an atom FQDN like ``sciona.atoms.ml.xgboost.feature_importance``,
    the 3rd-level namespace is ``ml``.

    We discover this by scanning src/sciona/atoms/ for subdirectories that
    contain Python files (i.e. actual atom packages).
    """
    atoms_dir = repo_path / "src" / "sciona" / "atoms"
    if not atoms_dir.is_dir():
        return set()

    namespaces: set[str] = set()
    for entry in atoms_dir.iterdir():
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_dir():
            # Check that it's a real package (has .py files, directly or nested)
            has_py = any(entry.rglob("*.py"))
            if has_py:
                namespaces.add(entry.name)
    return namespaces


def atom_namespace(atom: dict) -> str:
    """Extract the 3rd-level namespace from an atom dict."""
    fqdn = atom.get("atom_name") or atom.get("atom_key") or atom.get("fqdn", "")
    parts = fqdn.split(".")
    return parts[2] if len(parts) > 2 else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing files.",
    )
    args = parser.parse_args()

    # --- Phase 1: discover namespace ownership ---
    # Each repo owns whatever namespaces exist in its src/sciona/atoms/ tree.
    # If a namespace dir appears in multiple repos (e.g. signal_processing in
    # both sciona-atoms and sciona-atoms-signal), both repos keep their atoms.
    repo_namespaces: dict[str, set[str]] = {}
    for repo_name, repo_path in REPOS.items():
        repo_namespaces[repo_name] = discover_owned_namespaces(repo_path)

    print("=== Namespace ownership ===")
    for repo_name in sorted(repo_namespaces):
        ns = sorted(repo_namespaces[repo_name])
        print(f"  {repo_name}: {ns}")
    print()

    # --- Phase 2: filter manifests ---
    print(f"{'Repo':<28} {'Before':>7} {'After':>7} {'Removed':>8}")
    print("-" * 55)

    total_removed = 0
    for repo_name in sorted(REPOS):
        manifest_path = REPOS[repo_name] / "data" / "audit_manifest.json"
        if not manifest_path.exists():
            print(f"{repo_name:<28} {'(no manifest)':>24}")
            continue

        with open(manifest_path) as f:
            manifest = json.load(f)

        atoms_before = manifest.get("atoms", [])
        owned = repo_namespaces.get(repo_name, set())

        atoms_after = [a for a in atoms_before if atom_namespace(a) in owned]
        removed = len(atoms_before) - len(atoms_after)
        total_removed += removed

        # Show what namespaces were removed
        removed_atoms = [a for a in atoms_before if atom_namespace(a) not in owned]
        removed_ns = set(atom_namespace(a) for a in removed_atoms)

        status = ""
        if removed > 0:
            status = f"  (dropped: {', '.join(sorted(removed_ns))})"

        print(f"{repo_name:<28} {len(atoms_before):>7} {len(atoms_after):>7} {removed:>8}{status}")

        if not args.dry_run and removed > 0:
            manifest["atoms"] = atoms_after
            manifest["metadata"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generator": "scripts/reset_repo_manifests.py",
                "phase": "namespace_decontamination",
            }
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
                f.write("\n")

    print("-" * 55)
    print(f"{'TOTAL':<28} {'':>7} {'':>7} {total_removed:>8}")
    print()

    if args.dry_run:
        print("[DRY RUN] No files were modified.")
    else:
        if total_removed > 0:
            print(f"Done. Wrote updated manifests.")
        else:
            print("All manifests are already clean. No changes needed.")


if __name__ == "__main__":
    main()
