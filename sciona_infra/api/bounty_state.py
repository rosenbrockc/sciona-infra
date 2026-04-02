"""Bounty state machine — pure logic, no API or database dependencies."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


# ---------------------------------------------------------------------------
# Valid transitions: (current_status, target_status) -> action name
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "open"): "fund",
    ("draft", "cancelled"): "cancel_draft",
    ("open", "submitted"): "submit",
    ("open", "expired"): "expire",
    ("open", "cancelled"): "cancel_open",
    ("submitted", "verified"): "verify",
    ("submitted", "expired"): "expire",
    ("verified", "settled"): "settle",
}

# Reverse lookup: action -> target status
TARGET_STATUS: dict[str, str] = {
    "fund": "open",
    "cancel_draft": "cancelled",
    "submit": "submitted",
    "expire": "expired",
    "cancel_open": "cancelled",
    "verify": "verified",
    "settle": "settled",
}

ALL_STATUSES = frozenset(
    {"draft", "open", "submitted", "verified", "settled", "expired", "cancelled"}
)


class InvalidTransition(Exception):
    """Raised when a bounty state transition is not allowed."""


def validate_transition(current_status: str, action: str) -> str:
    """Validate and return the target status for a bounty transition.

    Parameters
    ----------
    current_status
        The current bounty status.
    action
        The action to perform (e.g. ``"fund"``, ``"submit"``).

    Returns
    -------
    str
        The new status after the transition.

    Raises
    ------
    InvalidTransition
        If the transition is not valid.
    """
    target = TARGET_STATUS.get(action)
    if target is None:
        raise InvalidTransition(f"Unknown action: {action!r}")

    key = (current_status, target)
    if key not in VALID_TRANSITIONS:
        raise InvalidTransition(
            f"Cannot {action!r} a bounty in {current_status!r} state"
        )

    return target


def compute_cancellation_fee(
    escrow_amount: Decimal,
    current_status: str,
    has_submissions: bool,
) -> Decimal:
    """Compute the cancellation fee for a bounty.

    Per design decision 4.13:
    - 10% if no submissions have been received.
    - 25% if submissions exist.

    Raises
    ------
    InvalidTransition
        If the bounty cannot be cancelled in its current state.
    """
    if current_status not in ("draft", "open"):
        raise InvalidTransition(
            f"Cannot cancel a bounty in {current_status!r} state"
        )

    if has_submissions:
        return escrow_amount * Decimal("0.25")
    return escrow_amount * Decimal("0.10")


def compute_payout_split(
    escrow_amount: Decimal,
) -> dict[str, Decimal]:
    """Compute the 5/65/30 payout split for a settled bounty.

    Returns a dict with keys ``platform``, ``architect``, ``originator_pool``.
    Uses exact decimal arithmetic to satisfy the payout conservation invariant.
    """
    platform = (escrow_amount * Decimal("5")) / Decimal("100")
    architect = (escrow_amount * Decimal("65")) / Decimal("100")
    originator_pool = escrow_amount - platform - architect  # residual assignment

    return {
        "platform": platform,
        "architect": architect,
        "originator_pool": originator_pool,
    }
