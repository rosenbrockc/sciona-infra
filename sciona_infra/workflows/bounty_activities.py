"""Temporal activities for the bounty lifecycle."""

from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

from sciona_infra.api.policy import evaluate_policy

try:
    from temporalio import activity as _activity
except ImportError:
    class _ActivityShim:
        def defn(self, fn):
            return fn

        @property
        def logger(self) -> logging.Logger:
            return logger

    _activity = _ActivityShim()

activity = _activity


@dataclass(frozen=True)
class RecordFundingInput:
    bounty_id: str
    stripe_payment_id: str


@dataclass(frozen=True)
class LaunchVerificationInput:
    bounty_id: str
    submission_id: str


@dataclass(frozen=True)
class ComputeSettlementInput:
    bounty_id: str
    escrow_amount: float
    verified_submission_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutePayoutsInput:
    bounty_id: str
    payout_plan_json: str


@dataclass(frozen=True)
class RecordSettlementInput:
    bounty_id: str
    transfer_ids: list[str] = field(default_factory=list)


@activity.defn
async def record_funding(input: RecordFundingInput) -> None:
    """Mark the bounty as open after funding is recorded."""
    supabase = await _get_supabase()
    if supabase is None:
        activity.logger.info("record_funding: no Supabase client available")
        return

    bounty = await _fetch_bounty(supabase, input.bounty_id)
    if bounty is None:
        activity.logger.warning("record_funding: bounty %s not found", input.bounty_id)
        return

    from sciona_infra.api.bounty_state import InvalidTransition, validate_transition

    try:
        validate_transition(str(bounty.get("status", "draft")), "fund")
    except InvalidTransition:
        activity.logger.info(
            "record_funding: bounty %s already moved past draft", input.bounty_id
        )
        return

    await (
        supabase.table("bounties")
        .update({"status": "open"})
        .eq("bounty_id", input.bounty_id)
        .execute()
    )


@activity.defn
async def launch_verification(input: LaunchVerificationInput) -> str:
    """Create a verification run record for a submission."""
    supabase = await _get_supabase()
    if supabase is None:
        activity.logger.info("launch_verification: no Supabase client available")
        return ""

    run_result = await (
        supabase.table("verification_runs")
        .insert(
            {
                "bounty_id": input.bounty_id,
                "submission_id": input.submission_id,
                "split_type": "public",
                "status": "pending",
            }
        )
        .execute()
    )
    created = _first_row(getattr(run_result, "data", None)) or {}
    run_id = str(created.get("id", ""))

    await (
        supabase.table("submissions")
        .update({"verification_status": "pending"})
        .eq("submission_id", input.submission_id)
        .execute()
    )

    return run_id


@activity.defn
async def compute_settlement(input: ComputeSettlementInput) -> str:
    """Compute a payout plan for the bounty."""
    supabase = await _get_supabase()
    if supabase is None:
        activity.logger.info("compute_settlement: no Supabase client available")
        from sciona.clearinghouse.models import PayoutPlan

        return PayoutPlan(
            bounty_id=input.bounty_id,
            escrow_amount=Decimal(str(input.escrow_amount)),
        ).model_dump_json()

    from sciona.clearinghouse.models import PayoutPlan, WinningCDG
    from sciona.clearinghouse.settlement import (
        compute_settlement as compute_settlement_plan,
        verify_payout_conservation,
    )

    bounty = await _fetch_bounty(supabase, input.bounty_id)
    escrow_amount = Decimal(str(input.escrow_amount))
    if bounty is not None:
        escrow_amount = Decimal(str(bounty.get("escrow_amount", input.escrow_amount)))

    submissions = []
    if input.verified_submission_ids:
        result = await (
            supabase.table("submissions")
            .select("*")
            .in_("submission_id", input.verified_submission_ids)
            .execute()
        )
        submissions = result.data or []

    if not submissions:
        return PayoutPlan(
            bounty_id=input.bounty_id,
            escrow_amount=escrow_amount,
        ).model_dump_json()

    winning_cdgs = [
        WinningCDG(
            submission_id=str(sub["submission_id"]),
            architect_id=str(sub["architect_id"]),
            cdg_hash=str(sub.get("cdg_hash", "")),
            atom_versions=dict(sub.get("atom_versions", {})),
            metric_values=dict(sub.get("metric_values", {})),
            weight=float(sub.get("weight", 1.0)),
        )
        for sub in submissions
    ]
    atom_dags: dict[str, dict[str, set[str]]] = {
        str(sub["cdg_hash"]): {} for sub in submissions if sub.get("cdg_hash")
    }

    plan = compute_settlement_plan(
        escrow_amount=escrow_amount,
        winning_cdgs=winning_cdgs,
        atom_dags=atom_dags,
        platform_account_id=os.getenv("STRIPE_PLATFORM_ACCOUNT", "platform"),
    )
    if not verify_payout_conservation(plan):
        raise RuntimeError("Payout conservation violated")

    plan_input = {
        "plan": {
            "escrow_amount": float(plan.escrow_amount),
            "recipients": [
                {
                    "recipient_id": recipient.recipient_id,
                    "role": recipient.role,
                    "amount": float(recipient.amount),
                    "stripe_account_id": recipient.stripe_account_id,
                }
                for recipient in plan.recipients
            ],
        }
    }
    if not await evaluate_policy("payout", "valid_plan", plan_input):
        raise RuntimeError("Payout plan failed OPA conservation validation")
    return plan.model_dump_json()


@activity.defn
async def execute_payouts(input: ExecutePayoutsInput) -> list[str]:
    """Execute payouts from a JSON payout plan."""
    from sciona.clearinghouse.models import PayoutPlan

    plan = PayoutPlan.model_validate_json(input.payout_plan_json)
    transfer_ids: list[str] = []

    try:
        import stripe
    except ImportError:
        stripe = None

    if stripe is None:
        for idx, recipient in enumerate(plan.recipients, start=1):
            transfer_ids.append(
                f"mock_tr_{input.bounty_id}_{idx}_{recipient.recipient_id}"
            )
        return transfer_ids

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    for recipient in plan.recipients:
        if recipient.role == "platform":
            transfer_ids.append(f"platform_retained_{input.bounty_id}")
            continue
        if not recipient.stripe_account_id:
            continue
        transfer = stripe.Transfer.create(
            amount=int(Decimal(recipient.amount) * Decimal("100")),
            currency="usd",
            destination=recipient.stripe_account_id,
            description=f"Bounty {input.bounty_id} payout ({recipient.role})",
            idempotency_key=f"bounty-{input.bounty_id}-{recipient.recipient_id}",
        )
        transfer_ids.append(transfer.id)

    return transfer_ids


@activity.defn
async def record_settlement(input: RecordSettlementInput) -> None:
    """Mark the bounty as settled."""
    supabase = await _get_supabase()
    if supabase is None:
        activity.logger.info("record_settlement: no Supabase client available")
        return

    await (
        supabase.table("bounties")
        .update({"status": "settled"})
        .eq("bounty_id", input.bounty_id)
        .execute()
    )


async def _get_supabase() -> Any | None:
    try:
        from supabase import acreate_client
    except ImportError:
        return None

    url = os.getenv("SCIONA_SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
    key = os.getenv(
        "SCIONA_SUPABASE_SERVICE_ROLE_KEY",
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    )
    if not url or not key:
        return None

    try:
        return await acreate_client(url, key)
    except Exception:
        logger.debug("Failed to create Supabase client for Temporal activity", exc_info=True)
        return None


async def _fetch_bounty(supabase: Any, bounty_id: str) -> dict[str, Any] | None:
    result = await (
        supabase.table("bounties")
        .select("*")
        .eq("bounty_id", bounty_id)
        .maybe_single()
        .execute()
    )
    return _first_row(getattr(result, "data", None))


def _first_row(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None
