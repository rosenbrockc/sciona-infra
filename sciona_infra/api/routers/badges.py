"""Badge catalog and progress API endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import (
    BadgeDefinitionResponse,
    BadgeMilestoneResponse,
    BadgeProgressResponse,
    BadgeTelemetryResponse,
    GrandmasterResponse,
    UserBadgeResponse,
)

router = APIRouter()


def _rows(data: Any) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


@router.get("/badges")
async def get_badge_catalog(
    supabase=Depends(api_deps.get_supabase),
) -> list[BadgeDefinitionResponse]:
    """Public badge catalog. Hidden badges excluded unless earned by caller."""
    result = await (
        supabase.table("badge_definitions")
        .select("*")
        .eq("is_hidden", False)
        .order("sort_order")
        .execute()
    )
    return [BadgeDefinitionResponse(**r) for r in _rows(result.data)]


@router.get("/badges/{badge_id}")
async def get_badge_detail(
    badge_id: str,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Single badge with milestones and rarity."""
    badge_result = await (
        supabase.table("badge_definitions")
        .select("*")
        .eq("badge_id", badge_id)
        .maybe_single()
        .execute()
    )
    badge = badge_result.data
    if isinstance(badge, list):
        badge = badge[0] if badge else None
    if not badge:
        raise HTTPException(404, "Badge not found")

    milestones_result = await (
        supabase.table("badge_milestones")
        .select("*")
        .eq("badge_id", badge_id)
        .execute()
    )
    milestones = []
    for ms in _rows(milestones_result.data):
        rarity = await supabase.rpc(
            "badge_rarity_percentile", {"p_milestone_id": ms["milestone_id"]}
        ).execute()
        milestones.append({
            **ms,
            "rarity_pct": float(rarity.data) if rarity.data is not None else 100.0,
        })

    return {
        "badge": BadgeDefinitionResponse(**badge),
        "milestones": milestones,
    }


@router.get("/users/{user_id}/badges")
async def get_user_badges(
    user_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> list[UserBadgeResponse]:
    """All earned badges for a user."""
    result = await (
        supabase.table("user_badges")
        .select("id, user_id, milestone_id, awarded_at, progress_value")
        .eq("user_id", str(user_id))
        .order("awarded_at", desc=True)
        .execute()
    )
    return [UserBadgeResponse(**r) for r in _rows(result.data)]


@router.get("/users/{user_id}/badges/progress")
async def get_user_badge_progress(
    user_id: UUID,
    user: api_deps.UserProfile = Depends(api_deps.require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> list[BadgeProgressResponse]:
    """Progress toward unearned badges (own user only)."""
    if str(user.user_id) != str(user_id):
        raise HTTPException(403, "Can only view own progress")

    result = await (
        supabase.table("badge_progress")
        .select("*")
        .eq("user_id", str(user_id))
        .execute()
    )
    return [BadgeProgressResponse(**r) for r in _rows(result.data)]


@router.get("/badges/{badge_id}/telemetry")
async def get_badge_telemetry(
    badge_id: str,
    user_id: str | None = None,
    supabase=Depends(api_deps.get_supabase),
) -> BadgeTelemetryResponse:
    """Live hover data: current value, rarity, top holders."""
    badge_result = await (
        supabase.table("badge_definitions")
        .select("badge_id, display_name")
        .eq("badge_id", badge_id)
        .maybe_single()
        .execute()
    )
    badge = badge_result.data
    if isinstance(badge, list):
        badge = badge[0] if badge else None
    if not badge:
        raise HTTPException(404, "Badge not found")

    current_value = 0.0
    if user_id:
        progress_result = await (
            supabase.table("badge_progress")
            .select("current_value")
            .eq("user_id", user_id)
            .eq("badge_id", badge_id)
            .maybe_single()
            .execute()
        )
        prog = progress_result.data
        if isinstance(prog, list):
            prog = prog[0] if prog else None
        if prog:
            current_value = float(prog["current_value"])

    # Count holders for top milestone
    milestones_result = await (
        supabase.table("badge_milestones")
        .select("milestone_id")
        .eq("badge_id", badge_id)
        .execute()
    )
    ms_ids = [m["milestone_id"] for m in _rows(milestones_result.data)]

    holder_count = 0
    if ms_ids:
        count_result = await (
            supabase.table("user_badges")
            .select("user_id", count="exact")
            .in_("milestone_id", ms_ids)
            .execute()
        )
        holder_count = int(count_result.count or 0)

    rarity_pct = 100.0
    if ms_ids:
        rarity = await supabase.rpc(
            "badge_rarity_percentile", {"p_milestone_id": ms_ids[0]}
        ).execute()
        rarity_pct = float(rarity.data) if rarity.data is not None else 100.0

    return BadgeTelemetryResponse(
        badge_id=badge_id,
        current_value=current_value,
        rarity_pct=rarity_pct,
        holder_count=holder_count,
    )


GRANDMASTER_TRACKS = ["originator", "architect", "vanguard", "evangelist"]


@router.get("/users/{user_id}/grandmaster")
async def get_grandmaster_status(
    user_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> GrandmasterResponse:
    """Check which tracks qualify for the grandmaster glowing ring."""
    earned_result = await (
        supabase.table("user_badges")
        .select("milestone_id")
        .eq("user_id", str(user_id))
        .execute()
    )
    earned_ids = {r["milestone_id"] for r in _rows(earned_result.data)}

    # Get all lattice/single milestones grouped by track
    all_milestones = await (
        supabase.table("badge_milestones")
        .select("milestone_id, badge_id, tier")
        .in_("tier", ["lattice", "single"])
        .execute()
    )
    all_badges = await (
        supabase.table("badge_definitions")
        .select("badge_id, track")
        .execute()
    )
    badge_track = {b["badge_id"]: b["track"] for b in _rows(all_badges.data)}

    track_qualified: dict[str, bool] = {}
    for track in GRANDMASTER_TRACKS:
        track_top_milestones = [
            m["milestone_id"]
            for m in _rows(all_milestones.data)
            if badge_track.get(m["badge_id"]) == track
        ]
        if not track_top_milestones:
            track_qualified[track] = False
            continue
        track_qualified[track] = all(mid in earned_ids for mid in track_top_milestones)

    return GrandmasterResponse(
        user_id=str(user_id),
        tracks=track_qualified,
        is_grandmaster=all(track_qualified.values()) and len(track_qualified) > 0,
    )
