"""Dashboard API endpoints — impact factor, benchmarks, BibTeX, compute preserved."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sciona_infra.api import deps as api_deps
from sciona.ecosystem.dashboard import (
    compute_impact_factor,
    estimate_compute_preserved,
    generate_bibtex,
)

router = APIRouter()


def _first_row(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


@router.get("/dashboard/originator/{originator_id}/impact")
async def get_originator_impact(
    originator_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Algorithmic Impact Factor for an originator."""
    impact_result = await supabase.rpc(
        "get_originator_impact", {"p_user_id": str(originator_id)}
    ).execute()
    row = _first_row(impact_result.data)
    if not row:
        raise HTTPException(404, "Originator not found")

    bounty_result = await supabase.rpc(
        "get_originator_bounty_values", {"p_user_id": str(originator_id)}
    ).execute()
    bounty_values = [
        float(r["escrow_amount"]) for r in (bounty_result.data or [])
    ]
    impact = compute_impact_factor(
        bounty_values,
        atom_count=int(row.get("atom_count", 0)),
        originator_id=str(originator_id),
        github_username=row.get("github_login", ""),
    )
    return impact.model_dump()


@router.get("/dashboard/atom/{fqdn}/benchmarks")
async def get_atom_benchmarks(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> list[dict]:
    """All benchmark results for an atom."""
    result = await supabase.rpc(
        "get_atom_benchmarks", {"p_fqdn": fqdn}
    ).execute()
    return list(result.data or [])


@router.get("/dashboard/atom/{fqdn}/bibtex")
async def get_atom_bibtex(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Auto-generated BibTeX entry for an atom."""
    atom_result = await (
        supabase.table("atoms")
        .select("atom_id, fqdn, description")
        .eq("fqdn", fqdn)
        .maybe_single()
        .execute()
    )
    atom = _first_row(atom_result.data)
    if not atom:
        raise HTTPException(404, "Atom not found")

    author_ids_result = await (
        supabase.table("atom_authors")
        .select("user_id")
        .eq("atom_id", atom["atom_id"])
        .execute()
    )
    author_ids = [str(r["user_id"]) for r in (author_ids_result.data or [])]
    authors = []
    if author_ids:
        author_rows = await (
            supabase.table("users")
            .select("github_login")
            .in_("user_id", author_ids)
            .execute()
        )
        authors = [r["github_login"] for r in (author_rows.data or [])]

    bibtex = generate_bibtex(
        atom_fqdn=fqdn,
        authors=authors,
        description=atom["description"],
    )

    # Track bibtex export for laureate badge (non-blocking, requires auth)
    try:
        from fastapi import Request as _Req
        # We don't require auth here, but if we have a token we can track the export
    except Exception:
        pass

    return {"fqdn": fqdn, "bibtex": bibtex}


@router.get("/dashboard/compute-preserved")
async def get_compute_preserved(
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Aggregate compute-preserved metrics."""
    result = await (
        supabase.table("compute_preserved")
        .select("bounty_id, escrow_amount, cdg_node_count")
        .execute()
    )
    rows = result.data or []

    bounties = [
        {
            "escrow_amount": float(r["escrow_amount"]),
            "cdg_node_count": r["cdg_node_count"],
        }
        for r in rows
    ]

    result = estimate_compute_preserved(bounties)
    return result.model_dump()


@router.get("/dashboard/leaderboard")
async def get_leaderboard(
    limit: int = 50,
    supabase=Depends(api_deps.get_supabase),
) -> list[dict]:
    """Top originators by impact factor."""
    result = await (
        supabase.table("originator_impact")
        .select(
            "originator_id, github_login, bounty_count, total_bounty_value, atom_count"
        )
        .order("total_bounty_value", desc=True)
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


@router.get("/dashboard/architect-leaderboard")
async def get_architect_leaderboard(
    limit: int = 50,
    supabase=Depends(api_deps.get_supabase),
) -> list[dict]:
    """Top architects by total earnings from winning CDGs."""
    result = await (
        supabase.table("architect_leaderboard")
        .select(
            "architect_id, github_login, submission_count, win_count, "
            "total_earned, bounties_won, distinct_atoms_used"
        )
        .order("total_earned", desc=True)
        .limit(limit)
        .execute()
    )
    return list(result.data or [])
