"""Catalog search and atom-document endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import CatalogEntry

router = APIRouter()


@router.get("/search")
async def catalog_search(
    q: str,
    domain_tag: str | None = None,
    limit: int = Query(default=50, le=200),
    supabase=Depends(api_deps.get_supabase),
) -> list[CatalogEntry]:
    """Full-text search across the atom catalog."""
    if q:
        try:
            rpc_result = await supabase.rpc(
                "search_atoms_hybrid",
                {
                    "query_text": q,
                    "mode": "fts",
                    "result_limit": limit,
                    "result_offset": 0,
                },
            ).execute()
            rows = rpc_result.data or []
            if domain_tag:
                rows = [
                    row
                    for row in rows
                    if domain_tag in (row.get("domain_tags") or [])
                ]
            return [
                CatalogEntry(
                    fqdn=row["fqdn"],
                    description=row.get("technical_description", "") or "",
                    domain_tags=row.get("domain_tags", []) or [],
                    status="approved",
                    overall_verdict=row.get("overall_verdict", "") or "",
                    risk_tier=row.get("risk_tier", "") or "",
                    trust_readiness=row.get("trust_readiness", "") or "",
                )
                for row in rows[:limit]
            ]
        except Exception:
            pass
    query = supabase.table("catalog_atoms_served").select(
        "fqdn, technical_description, domain_tags, overall_verdict, risk_tier, trust_readiness"
    )
    if q:
        query = query.or_(
            f"fqdn.ilike.%{q}%,technical_description.ilike.%{q}%"
        )
    if domain_tag:
        query = query.contains("domain_tags", [domain_tag])
    result = await query.limit(limit).execute()
    return [
        CatalogEntry(
            fqdn=row["fqdn"],
            description=row.get("technical_description", "") or "",
            domain_tags=row.get("domain_tags", []) or [],
            status="approved",
            overall_verdict=row.get("overall_verdict", "") or "",
            risk_tier=row.get("risk_tier", "") or "",
            trust_readiness=row.get("trust_readiness", "") or "",
        )
        for row in (result.data or [])
    ]


@router.get("/atom/{fqdn:path}")
async def get_atom_document(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Fetch the full atom documentation bundle via the database RPC."""
    result = await supabase.rpc(
        "get_atom_document",
        {"request_fqdn": fqdn},
    ).execute()
    document = result.data
    if not document:
        raise HTTPException(404, f"Atom {fqdn!r} not found")
    return document
