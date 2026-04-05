"""Verification and settlement API endpoints."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import PaginatedResponse
from sciona.clearinghouse.models import LeaderboardEntry
from sciona_infra.workflows import BountyWorkflow

router = APIRouter()
logger = logging.getLogger(__name__)


def _first_row(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _current_span():
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    try:
        return trace.get_current_span()
    except Exception:
        return None


def _annotate_span(**attributes: Any) -> None:
    span = _current_span()
    if span is None:
        return
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            logger.debug("Failed to set span attribute %s", key, exc_info=True)


@router.get("/submissions/{submission_id}/status")
async def get_submission_status(
    submission_id: UUID,
    temporal=Depends(api_deps.get_temporal),
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Poll verification progress for a submission."""
    _annotate_span(
        **{
            "verification.action": "status_poll",
            "submission.id": str(submission_id),
        }
    )
    submission_result = await (
        supabase.table("submissions")
        .select("*")
        .eq("submission_id", str(submission_id))
        .maybe_single()
        .execute()
    )
    submission = _first_row(submission_result.data)
    if not submission:
        raise HTTPException(404, "Submission not found")
    bounty_id = str(submission.get("bounty_id", ""))
    _annotate_span(**{"bounty.id": bounty_id})

    workflow_status = None
    if temporal is not None and bounty_id:
        try:
            handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
            workflow_status = await handle.query(BountyWorkflow.get_status)
        except Exception:
            logger.debug(
                "Temporal workflow query failed for submission %s",
                submission_id,
                exc_info=True,
            )

    runs_result = await (
        supabase.table("verification_runs")
        .select("status, metric_values, output_hash, is_deterministic")
        .eq("submission_id", str(submission_id))
        .order("created_at", desc=True)
        .execute()
    )
    return {
        "submission_id": str(submission_id),
        "verification_status": str(workflow_status or submission["verification_status"]),
        "runs": runs_result.data or [],
    }


@router.get("/bounties/{bounty_id}/leaderboard")
async def get_leaderboard(
    bounty_id: UUID,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(api_deps.get_supabase),
) -> PaginatedResponse:
    """Current ranking of verified submissions for a bounty."""
    _annotate_span(
        **{
            "verification.action": "leaderboard",
            "bounty.id": str(bounty_id),
            "verification.limit": limit,
            "verification.offset": offset,
        }
    )
    limit = int(getattr(limit, "default", limit))
    offset = int(getattr(offset, "default", offset))

    bounty_result = await (
        supabase.table("bounties")
        .select("bounty_id")
        .eq("bounty_id", str(bounty_id))
        .maybe_single()
        .execute()
    )
    if not _first_row(bounty_result.data):
        raise HTTPException(404, "Bounty not found")

    rpc_result = await supabase.rpc(
        "get_bounty_leaderboard",
        {
            "p_bounty_id": str(bounty_id),
            "p_limit": limit,
            "p_offset": offset,
        },
    ).execute()
    rows = rpc_result.data or []
    total = int(rows[0]["total_count"]) if rows else 0
    items = [
        LeaderboardEntry(
            submission_id=str(r["submission_id"]),
            architect_id=str(r["architect_id"]),
            metric_values=r["metric_values"] or {},
            verified_at=r["verified_at"],
            rank=offset + i + 1,
        )
        for i, r in enumerate(rows)
    ]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/bounties/{bounty_id}/settlement")
async def get_settlement(
    bounty_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Retrieve settlement details for a settled bounty."""
    _annotate_span(**{"verification.action": "settlement", "bounty.id": str(bounty_id)})
    bounty_result = await (
        supabase.table("bounties")
        .select("*")
        .eq("bounty_id", str(bounty_id))
        .maybe_single()
        .execute()
    )
    row = _first_row(bounty_result.data)
    if not row:
        raise HTTPException(404, "Bounty not found")
    if row["status"] != "settled":
        raise HTTPException(409, "Bounty is not yet settled")

    payouts_result = await (
        supabase.table("settlement_payouts")
        .select("recipient_id, role, amount, atom_fqdn, cdg_hash")
        .eq("bounty_id", str(bounty_id))
        .order("role")
        .order("amount", desc=True)
        .execute()
    )
    settlement = {
        "bounty_id": str(bounty_id),
        "status": "settled",
        "escrow_amount": float(row["escrow_amount"]),
        "payouts": payouts_result.data or [],
    }

    # Badge + referral hooks for all payout recipients (non-blocking)
    try:
        from sciona_infra.api.badges import evaluate_badges_for_user, check_referral_value
        for payout in payouts_result.data or []:
            recipient = str(payout.get("recipient_id", ""))
            if recipient:
                role = payout.get("role", "")
                if role == "originator":
                    await evaluate_badges_for_user(supabase, recipient, "payout_settled")
                elif role == "architect":
                    await evaluate_badges_for_user(supabase, recipient, "bounty_won")
                    await check_referral_value(supabase, recipient, "bounty_won")
    except Exception:
        pass

    return settlement
