"""Shared helpers for Supabase backfill scripts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client


DEFAULT_ATOMS_ROOT = "../ageo-atoms/ageoa"


def atoms_root_from_env() -> Path:
    """Return the configured atom-source root used for file backfills."""
    return Path(os.environ.get("AGEOA_ATOMS_ROOT", DEFAULT_ATOMS_ROOT))


def namespace_from_path(file_path: Path) -> str:
    """Derive the dotted namespace from a Phase 2D source file path."""
    parts = file_path.parent.parts
    clean: list[str] = []
    for part in parts:
        if part == "_artifacts":
            break
        clean.append(part)
    return ".".join(clean)


def resolve_atom_id(supabase: "Client", namespace: str, short_name: str) -> str | None:
    """Resolve an atom short name + namespace into an atom_id via exact then suffix lookup."""
    fqdn = f"{namespace}.{short_name}"
    response = (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]["atom_id"]

    response = (
        supabase.table("atoms")
        .select("atom_id")
        .like("fqdn", f"%.{short_name}")
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]["atom_id"]

    return None


def create_supabase_client_from_env() -> Any:
    """Create a service-role Supabase client from environment variables."""
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
