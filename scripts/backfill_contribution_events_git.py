"""Generate contribution_events from git history in the ageo-atoms repository."""

from __future__ import annotations

import logging
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

log = logging.getLogger(__name__)

DEFAULT_REPO_PATH = "../ageo-atoms"


def create_supabase_client() -> "Client":
    """Create a service-role Supabase client from environment variables."""
    from supabase import create_client

    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(
        os.environ["SUPABASE_URL"],
        service_key,
    )


def get_git_log(repo_path: Path) -> str:
    """Extract added/modified commits that touch atom source files."""
    result = subprocess.run(
        [
            "git",
            "log",
            "--format=%H|%ae|%aI",
            "--diff-filter=AM",
            "--name-only",
            "--",
            "ageoa/*/atoms.py",
        ],
        capture_output=True,
        text=True,
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git log failed")
    return result.stdout


def parse_git_log(raw_log: str) -> list[dict[str, Any]]:
    """Parse git log output into commit/email/date/file groups."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw_log.strip().splitlines():
        if "|" in line and line.count("|") == 2:
            if current:
                entries.append(current)
            sha, email, date_str = line.split("|", 2)
            current = {"sha": sha, "email": email, "date": date_str, "files": []}
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    if current:
        entries.append(current)
    return entries


def derive_atom_family_from_path(file_path: str) -> str | None:
    """Derive the atom family from an ageoa source path."""
    parts = file_path.split("/")
    if len(parts) >= 2 and parts[0] == "ageoa":
        return parts[1]
    return None


def build_user_family_events(
    entries: list[dict[str, Any]],
    email_to_user: dict[str, str],
) -> dict[tuple[str, str], dict[str, str]]:
    """Collapse git history to earliest commit per (user, family)."""
    grouped: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for entry in entries:
        user_id = email_to_user.get(entry["email"].lower())
        if not user_id:
            continue
        for file_path in entry["files"]:
            family = derive_atom_family_from_path(file_path)
            if not family:
                continue
            key = (user_id, family)
            existing = grouped.get(key)
            if not existing or entry["date"] < existing["date"]:
                grouped[key] = {"sha": entry["sha"], "date": entry["date"]}
    return grouped


def matching_atoms_for_family(fqdn_map: dict[str, dict[str, Any]], family: str) -> list[dict[str, Any]]:
    """Return atoms that appear to belong to a family path segment."""
    return [
        atom
        for fqdn, atom in fqdn_map.items()
        if f".{family}." in fqdn or fqdn.endswith(f".{family}")
    ]


def main() -> int:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    repo_path = Path(os.environ.get("AGEO_ATOMS_REPO_PATH", DEFAULT_REPO_PATH))
    supabase = create_supabase_client()

    raw_log = get_git_log(repo_path)
    entries = parse_git_log(raw_log)

    users_resp = supabase.table("users").select("user_id, email").execute()
    email_to_user = {
        row["email"].lower(): row["user_id"]
        for row in users_resp.data or []
        if row.get("email")
    }

    atoms_resp = supabase.table("atoms").select("atom_id, fqdn").execute()
    fqdn_map = {row["fqdn"]: row for row in atoms_resp.data or []}

    user_family_events = build_user_family_events(entries, email_to_user)
    inserted = 0
    skipped = 0

    for (user_id, family), event_data in user_family_events.items():
        matching_atoms = matching_atoms_for_family(fqdn_map, family)
        if not matching_atoms:
            skipped += 1
            continue
        for atom in matching_atoms:
            row = {
                "user_id": user_id,
                "event_kind": "atom_authorship",
                "entity_kind": "atom",
                "entity_id": atom["atom_id"],
                "entity_fqdn": atom["fqdn"],
                "approved_at": event_data["date"],
                "source": "git_history",
                "source_ref": event_data["sha"],
            }
            try:
                (
                    supabase.table("contribution_events")
                    .upsert(row, on_conflict="user_id,event_kind,entity_id")
                    .execute()
                )
                inserted += 1
            except Exception:
                log.exception("Failed to upsert contribution event for %s / %s", user_id, atom["fqdn"])
                skipped += 1

    print(f"Git contribution events inserted: {inserted}")
    print(f"Skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
