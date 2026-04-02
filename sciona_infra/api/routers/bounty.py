"""Bounty lifecycle endpoints."""

from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sciona_infra.api import deps as api_deps
from sciona_infra.api.bounty_state import (
    InvalidTransition,
    compute_cancellation_fee,
    validate_transition,
)
from sciona_infra.api.policy import PolicyDenied, require_policy
from sciona_infra.api.models import (
    BountyCancelResponse,
    BountyCreateRequest,
    BountyFundResponse,
    BountyResponse,
    BountySummaryResponse,
    PaginatedResponse,
    SubmissionRequest,
    SubmissionResponse,
    UpdateTargetRequest,
)
from sciona_infra.workflows import BountyWorkflow, BountyWorkflowInput

UserRow = getattr(api_deps, "UserProfile", None) or api_deps.UserRow
require_auth = api_deps.require_auth

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


def _json_obj(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return value


def _opa_input(
    user: UserRow,
    bounty: dict[str, Any] | None = None,
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the OPA input document from the caller and optional payloads."""
    doc: dict[str, Any] = {
        "user": {
            "user_id": str(getattr(user, "user_id", "")),
            "identity_tier": getattr(user, "identity_tier", "contributor"),
            "effective_tier": getattr(user, "effective_tier", "general"),
            "is_blacklisted": bool(getattr(user, "is_blacklisted", False)),
            "reputation_score": int(getattr(user, "reputation_score", 0)),
        }
    }
    if bounty is not None:
        doc["bounty"] = {
            "bounty_id": str(bounty.get("bounty_id", "")),
            "principal_id": str(bounty.get("principal_id", "")),
            "status": str(bounty.get("status", "")),
            "escrow_amount": float(bounty.get("escrow_amount", 0)),
            "tier": str(bounty.get("tier", "standard")),
        }
    if submission is not None:
        doc["submission"] = {
            "receipt_json": submission.get("receipt_json", {}),
            "receipt_s3": submission.get("receipt_s3", ""),
        }
    return doc


async def _enforce(package: str, rule: str, input_data: dict[str, Any]) -> None:
    try:
        await require_policy(package, rule, input_data)
    except PolicyDenied as exc:
        raise HTTPException(403, f"Policy denied: {exc}") from exc


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


@router.post("")
async def create_bounty(
    body: BountyCreateRequest,
    user: UserRow = Depends(require_auth),
    temporal=Depends(api_deps.get_temporal),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Create a draft bounty."""
    await _enforce("bounty", "allow_create", _opa_input(user))
    user_id = str(user.user_id)
    _annotate_span(
        **{
            "bounty.action": "create",
            "bounty.principal_id": user_id,
            "bounty.tier": body.tier,
        }
    )
    result = await (
        supabase.table("bounties")
        .insert(
            {
                "principal_id": user_id,
                "title": body.title,
                "escrow_amount": body.escrow_amount,
                "deadline": body.deadline,
                "tier": body.tier,
                "config_yml": body.config_yml,
                "flare_payload": body.flare_payload,
            }
        )
        .execute()
    )
    created = _first_row(result.data)
    if not created:
        raise HTTPException(500, "Failed to create bounty")
    await _start_bounty_workflow(
        temporal,
        bounty_id=created["bounty_id"],
        principal_id=user_id,
        escrow_amount=float(created["escrow_amount"]),
        deadline=body.deadline,
    )
    return _bounty_response(created)


@router.post("/{bounty_id}/fund")
async def fund_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    temporal=Depends(api_deps.get_temporal),
    supabase=Depends(api_deps.get_supabase),
) -> BountyFundResponse:
    """Fund a bounty (transitions draft -> open)."""
    _annotate_span(
        **{
            "bounty.action": "fund",
            "bounty.id": str(bounty_id),
            "user.id": str(user.user_id),
        }
    )
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    await _enforce("bounty", "allow_fund", _opa_input(user, row))

    try:
        new_status = validate_transition(row["status"], "fund")
    except InvalidTransition as e:
        raise HTTPException(409, str(e))

    await (
        supabase.table("bounties")
        .update({"status": new_status})
        .eq("bounty_id", str(bounty_id))
        .execute()
    )
    await _signal_bounty_workflow(
        temporal,
        bounty_id=bounty_id,
        signal_name="fund",
        stripe_payment_id="",
    )

    return BountyFundResponse(
        bounty_id=bounty_id,
        status=new_status,
        checkout_url="",
    )


@router.post("/{bounty_id}/submit")
async def submit_to_bounty(
    bounty_id: UUID,
    body: SubmissionRequest,
    user: UserRow = Depends(require_auth),
    temporal=Depends(api_deps.get_temporal),
    supabase=Depends(api_deps.get_supabase),
) -> SubmissionResponse:
    """Submit a CDG solution with signed receipt."""
    _annotate_span(
        **{
            "bounty.action": "submit",
            "bounty.id": str(bounty_id),
            "user.id": str(user.user_id),
        }
    )
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    await _enforce("bounty", "allow_submit", _opa_input(user, row))
    await _enforce(
        "submission",
        "allow",
        _opa_input(
            user,
            row,
            {"receipt_json": body.receipt_json, "receipt_s3": ""},
        ),
    )

    if row["status"] == "open":
        try:
            validate_transition(row["status"], "submit")
        except InvalidTransition as e:
            raise HTTPException(409, str(e))
        await (
            supabase.table("bounties")
            .update({"status": "submitted"})
            .eq("bounty_id", str(bounty_id))
            .execute()
        )
    elif row["status"] != "submitted":
        raise HTTPException(409, f"Cannot submit to bounty in {row['status']!r} state")

    sub_result = await (
        supabase.table("submissions")
        .insert(
            {
                "bounty_id": str(bounty_id),
                "architect_id": str(user.user_id),
                "cdg_hash": body.cdg_hash,
                "atom_versions": body.atom_versions,
                "receipt_s3": "",
                "receipt_json": body.receipt_json,
                "claimed_metric_name": body.claimed_metric_name,
                "claimed_metric_value": body.claimed_metric_value,
            }
        )
        .execute()
    )
    created = _first_row(sub_result.data)
    if not created:
        raise HTTPException(500, "Failed to create submission")
    sub_row = await (
        supabase.table("submissions")
        .select("submission_id, bounty_id, verification_status, submitted_at")
        .eq("submission_id", created["submission_id"])
        .maybe_single()
        .execute()
    )
    row = _first_row(sub_row.data) or created
    await _signal_bounty_workflow(
        temporal,
        bounty_id=bounty_id,
        signal_name="submit",
        submission_id=str(row["submission_id"]),
        architect_id=str(user.user_id),
    )
    return SubmissionResponse(**dict(row))


@router.post("/{bounty_id}/cancel")
async def cancel_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    temporal=Depends(api_deps.get_temporal),
    supabase=Depends(api_deps.get_supabase),
) -> BountyCancelResponse:
    """Cancel a bounty (with fee per design decision 4.13)."""
    _annotate_span(
        **{
            "bounty.action": "cancel",
            "bounty.id": str(bounty_id),
            "user.id": str(user.user_id),
        }
    )
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    await _enforce("bounty", "allow_cancel", _opa_input(user, row))

    submissions_result = await (
        supabase.table("submissions")
        .select("submission_id", count="exact")
        .eq("bounty_id", str(bounty_id))
        .execute()
    )
    has_submissions = bool(submissions_result.count or submissions_result.data)

    try:
        action = "cancel_open" if row["status"] == "open" else "cancel_draft"
        new_status = validate_transition(row["status"], action)
        fee = compute_cancellation_fee(
            Decimal(str(row["escrow_amount"])),
            row["status"],
            has_submissions,
        )
    except InvalidTransition as e:
        raise HTTPException(409, str(e))

    await (
        supabase.table("bounties")
        .update({"status": new_status, "cancellation_fee": float(fee)})
        .eq("bounty_id", str(bounty_id))
        .execute()
    )
    await _signal_bounty_workflow(
        temporal,
        bounty_id=bounty_id,
        signal_name="cancel",
    )

    return BountyCancelResponse(
        bounty_id=bounty_id,
        status=new_status,
        cancellation_fee=float(fee),
    )


@router.post("/{bounty_id}/target")
async def update_target(
    bounty_id: UUID,
    body: UpdateTargetRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Principal updates minimum metric target between verifications."""
    _annotate_span(
        **{
            "bounty.action": "update_target",
            "bounty.id": str(bounty_id),
            "user.id": str(user.user_id),
        }
    )
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    await _enforce("bounty", "allow_update_target", _opa_input(user, row))
    if row["status"] not in ("open", "submitted"):
        raise HTTPException(409, "Can only update target for open/submitted bounties")

    config = _json_obj(row["config_yml"])
    config["min_metric_value"] = body.min_metric_value

    await (
        supabase.table("bounties")
        .update({"config_yml": config})
        .eq("bounty_id", str(bounty_id))
        .execute()
    )
    updated = await _fetch_bounty(bounty_id, supabase=supabase)
    return _bounty_response(updated)


@router.get("/{bounty_id}")
async def get_bounty(
    bounty_id: UUID,
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Get bounty details including submission count and status."""
    _annotate_span(**{"bounty.action": "get", "bounty.id": str(bounty_id)})
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    return _bounty_response(row, bounty_id=bounty_id)


@router.get("")
async def list_bounties(
    status: str | None = None,
    domain_tag: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    supabase=Depends(api_deps.get_supabase),
) -> PaginatedResponse:
    """List bounties with optional filters."""
    _annotate_span(
        **{
            "bounty.action": "list",
            "bounty.status": status,
            "bounty.domain_tag": domain_tag,
            "bounty.limit": limit,
            "bounty.offset": offset,
        }
    )
    limit = int(getattr(limit, "default", limit))
    offset = int(getattr(offset, "default", offset))

    query = supabase.table("bounties").select(
        "bounty_id, title, escrow_amount, status, deadline, tier, created_at",
        count="exact",
    )
    if status:
        query = query.eq("status", status)
    # Domain tags are not stored on bounties in the current schema; keep the
    # parameter for API compatibility but do not filter on it here.
    result = await query.order("created_at", desc=True).range(
        offset, offset + limit - 1
    ).execute()
    rows = result.data or []
    total = int(result.count or len(rows))

    items = [
        BountySummaryResponse(
            bounty_id=r["bounty_id"],
            title=r["title"],
            escrow_amount=float(r["escrow_amount"]),
            status=r["status"],
            deadline=r["deadline"],
            tier=r["tier"],
        )
        for r in rows
    ]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


def _bounty_response(row, *, bounty_id=None) -> BountyResponse:
    """Convert a database row to a BountyResponse."""
    return BountyResponse(
        bounty_id=row["bounty_id"],
        principal_id=row["principal_id"],
        title=row["title"],
        escrow_amount=float(row["escrow_amount"]),
        status=row["status"],
        deadline=row["deadline"],
        tier=row["tier"],
        verification_budget=row["verification_budget"],
        verifications_used=row["verifications_used"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _workflow_id(bounty_id: UUID | str) -> str:
    return f"bounty-{bounty_id}"


async def _start_bounty_workflow(
    temporal: Any | None,
    *,
    bounty_id: UUID | str,
    principal_id: str,
    escrow_amount: float,
    deadline: datetime | None,
) -> None:
    if temporal is None:
        return
    try:
        deadline_seconds = 0
        if deadline is not None:
            deadline_utc = (
                deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=timezone.utc)
            )
            deadline_seconds = max(
                int((deadline_utc - datetime.now(timezone.utc)).total_seconds()),
                0,
            )
        await temporal.start_workflow(
            BountyWorkflow.run,
            args=[
                BountyWorkflowInput(
                    bounty_id=str(bounty_id),
                    escrow_amount=float(escrow_amount),
                    principal_id=principal_id,
                    deadline_seconds=deadline_seconds,
                )
            ],
            id=_workflow_id(bounty_id),
            task_queue="bounty-lifecycle",
        )
    except Exception:
        logger.debug("Temporal workflow start failed for bounty %s", bounty_id, exc_info=True)


async def _signal_bounty_workflow(
    temporal: Any | None,
    *,
    bounty_id: UUID | str,
    signal_name: str,
    **payload: Any,
) -> None:
    if temporal is None:
        return
    try:
        handle = temporal.get_workflow_handle(_workflow_id(bounty_id))
        signal = getattr(BountyWorkflow, signal_name)
        await handle.signal(signal, **payload)
    except Exception:
        logger.debug(
            "Temporal workflow signal failed for bounty %s (%s)",
            bounty_id,
            signal_name,
            exc_info=True,
        )


async def _fetch_bounty(
    bounty_id: UUID,
    *,
    supabase,
) -> dict[str, Any] | None:
    result = await (
        supabase.table("bounties")
        .select("*")
        .eq("bounty_id", str(bounty_id))
        .maybe_single()
        .execute()
    )
    return _first_row(result.data)
