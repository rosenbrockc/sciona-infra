"""Heuristic registry and CDG binding evidence endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import (
    BindingEvidenceResponse,
    HeuristicRegistryEntry,
)

router = APIRouter()


@router.get("")
async def list_heuristics(
    domain: str,
    family: str | None = None,
    supabase=Depends(api_deps.get_supabase),
) -> list[HeuristicRegistryEntry]:
    """Query heuristic registry by domain and optional family."""
    result = await supabase.rpc(
        "get_heuristics_by_domain",
        {"request_domain": domain, "request_family": family},
    ).execute()
    rows = result.data or []
    if isinstance(rows, str):
        import json
        rows = json.loads(rows)
    if not isinstance(rows, list):
        rows = []
    return [HeuristicRegistryEntry(**r) for r in rows]


@router.get("/bindings/{version_id}/evidence")
async def get_binding_evidence(
    version_id: UUID,
    node_id: str | None = Query(default=None),
    supabase=Depends(api_deps.get_supabase),
) -> list[BindingEvidenceResponse]:
    """Query per-heuristic binding evidence for a CDG version."""
    result = await supabase.rpc(
        "get_binding_evidence",
        {"request_version_id": str(version_id), "request_node_id": node_id},
    ).execute()
    rows = result.data or []
    if isinstance(rows, str):
        import json
        rows = json.loads(rows)
    if not isinstance(rows, list):
        rows = []
    return [BindingEvidenceResponse(**r) for r in rows]
