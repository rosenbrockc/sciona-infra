"""Atom registry CRUD endpoints."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import (
    AtomDetailResponse,
    AtomPublishRequest,
    AtomPublishResponse,
    AtomSummaryResponse,
    AtomVersionResponse,
    PaginatedResponse,
)

UserRow = getattr(api_deps, "UserProfile", None) or api_deps.UserRow
require_auth = api_deps.require_auth

router = APIRouter()


def _first_row(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


@router.post("")
async def publish_atom(
    body: AtomPublishRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> AtomPublishResponse:
    """Publish a new atom or new version of an existing atom."""
    import base64

    source_bytes = base64.b64decode(body.source_tar_b64)
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    user_id = str(user.user_id)

    existing = await (
        supabase.table("atom_versions")
        .select("version_id")
        .eq("content_hash", content_hash)
        .maybe_single()
        .execute()
    )
    if _first_row(existing.data):
        raise HTTPException(409, f"Content hash {content_hash[:16]}… already exists")

    atom_result = await (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", body.fqdn)
        .maybe_single()
        .execute()
    )
    atom_row = _first_row(atom_result.data)
    is_new = atom_row is None
    if is_new:
        inserted = await (
            supabase.table("atoms")
            .insert(
                {
                    "fqdn": body.fqdn,
                    "owner_id": user_id,
                    "domain_tags": body.domain_tags,
                    "description": body.description,
                }
            )
            .execute()
        )
        atom_row = _first_row(inserted.data)
    if not atom_row:
        raise HTTPException(500, "Failed to create atom")

    atom_id = atom_row["atom_id"]
    await (
        supabase.table("atom_versions")
        .update({"is_latest": False})
        .eq("atom_id", atom_id)
        .execute()
    )
    version_row = _first_row(
        (
            await (
                supabase.table("atom_versions")
                .insert(
                    {
                        "atom_id": atom_id,
                        "content_hash": content_hash,
                        "semver": body.semver,
                        "is_latest": True,
                        "s3_key": f"atoms/{content_hash}.tar.gz",
                        "fingerprint": body.fingerprint,
                    }
                )
                .execute()
            )
        ).data
    )
    if not version_row:
        raise HTTPException(500, "Failed to create version")

    return AtomPublishResponse(
        atom_id=atom_id,
        version_id=version_row["version_id"],
        fqdn=body.fqdn,
        content_hash=content_hash,
        semver=body.semver,
        is_new_atom=is_new,
    )


@router.get("/{fqdn:path}/versions")
async def list_versions(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> list[AtomVersionResponse]:
    """List all versions of an atom."""
    atom_result = await (
        supabase.table("atoms")
        .select("atom_id")
        .eq("fqdn", fqdn)
        .maybe_single()
        .execute()
    )
    atom_row = _first_row(atom_result.data)
    if not atom_row:
        return []

    rows = await (
        supabase.table("atom_versions")
        .select("version_id, content_hash, semver, is_latest, fingerprint, created_at")
        .eq("atom_id", atom_row["atom_id"])
        .order("created_at", desc=True)
        .execute()
    )
    return [AtomVersionResponse(**dict(r)) for r in (rows.data or [])]


@router.get("")
async def search_atoms(
    q: str = "",
    domain_tag: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(api_deps.get_supabase),
) -> PaginatedResponse:
    """Search/list atoms with optional filters."""
    limit = int(getattr(limit, "default", limit))
    offset = int(getattr(offset, "default", offset))

    query = (
        supabase.table("atoms")
        .select("atom_id, fqdn, description, domain_tags, status", count="exact")
        .eq("status", "approved")
    )
    if q:
        query = query.or_(f"fqdn.ilike.%{q}%,description.ilike.%{q}%")
    if domain_tag:
        query = query.contains("domain_tags", [domain_tag])
    result = await query.order("updated_at", desc=True).range(
        offset, offset + limit - 1
    ).execute()
    rows = result.data or []
    total = int(result.count or len(rows))

    items = [
        AtomSummaryResponse(
            atom_id=r["atom_id"],
            fqdn=r["fqdn"],
            description=r["description"],
            domain_tags=r["domain_tags"],
            status=r["status"],
        )
        for r in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{fqdn:path}")
async def get_atom(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> AtomDetailResponse:
    """Get atom metadata + latest version."""
    atom_result = await (
        supabase.table("atoms")
        .select(
            "atom_id, fqdn, description, domain_tags, status, created_at, owner_id"
        )
        .eq("fqdn", fqdn)
        .maybe_single()
        .execute()
    )
    atom = _first_row(atom_result.data)
    if not atom:
        raise HTTPException(404, f"Atom {fqdn!r} not found")

    owner_result = await (
        supabase.table("users")
        .select("github_login")
        .eq("user_id", atom["owner_id"])
        .maybe_single()
        .execute()
    )
    owner = _first_row(owner_result.data)
    if not owner:
        raise HTTPException(404, f"Atom {fqdn!r} not found")

    latest_result = await (
        supabase.table("atom_versions")
        .select("version_id, content_hash, semver, is_latest, fingerprint, created_at")
        .eq("atom_id", atom["atom_id"])
        .eq("is_latest", True)
        .maybe_single()
        .execute()
    )
    latest = _first_row(latest_result.data)

    return AtomDetailResponse(
        atom_id=atom["atom_id"],
        fqdn=atom["fqdn"],
        description=atom["description"],
        domain_tags=atom["domain_tags"],
        status=atom["status"],
        owner_github_login=owner["github_login"],
        latest_version=AtomVersionResponse(**dict(latest)) if latest else None,
        created_at=atom["created_at"],
    )
