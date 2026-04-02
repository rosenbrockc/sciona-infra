"""Tests for the bounty state machine (pure logic, no API or database)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sciona_infra.api.bounty_state import (
    ALL_STATUSES,
    InvalidTransition,
    TARGET_STATUS,
    VALID_TRANSITIONS,
    compute_cancellation_fee,
    compute_payout_split,
    validate_transition,
)


class TestValidTransitions:
    def test_draft_to_open(self):
        assert validate_transition("draft", "fund") == "open"

    def test_draft_to_cancelled(self):
        assert validate_transition("draft", "cancel_draft") == "cancelled"

    def test_open_to_submitted(self):
        assert validate_transition("open", "submit") == "submitted"

    def test_open_to_expired(self):
        assert validate_transition("open", "expire") == "expired"

    def test_open_to_cancelled(self):
        assert validate_transition("open", "cancel_open") == "cancelled"

    def test_submitted_to_verified(self):
        assert validate_transition("submitted", "verify") == "verified"

    def test_submitted_to_expired(self):
        assert validate_transition("submitted", "expire") == "expired"

    def test_verified_to_settled(self):
        assert validate_transition("verified", "settle") == "settled"


class TestInvalidTransitions:
    def test_cannot_fund_open(self):
        with pytest.raises(InvalidTransition):
            validate_transition("open", "fund")

    def test_cannot_submit_to_draft(self):
        with pytest.raises(InvalidTransition):
            validate_transition("draft", "submit")

    def test_cannot_settle_submitted(self):
        with pytest.raises(InvalidTransition):
            validate_transition("submitted", "settle")

    def test_cannot_cancel_verified(self):
        with pytest.raises(InvalidTransition):
            validate_transition("verified", "cancel_open")

    def test_cannot_cancel_settled(self):
        with pytest.raises(InvalidTransition):
            validate_transition("settled", "cancel_draft")

    def test_unknown_action(self):
        with pytest.raises(InvalidTransition, match="Unknown action"):
            validate_transition("draft", "nonexistent_action")

    def test_expired_is_terminal(self):
        for action in TARGET_STATUS:
            if action == "expire":
                continue
            with pytest.raises(InvalidTransition):
                validate_transition("expired", action)

    def test_settled_is_terminal(self):
        for action in TARGET_STATUS:
            with pytest.raises(InvalidTransition):
                validate_transition("settled", action)

    def test_cancelled_is_terminal(self):
        for action in TARGET_STATUS:
            with pytest.raises(InvalidTransition):
                validate_transition("cancelled", action)


class TestCancellationFee:
    def test_no_submissions(self):
        fee = compute_cancellation_fee(Decimal("100.00"), "open", False)
        assert fee == Decimal("10.00")

    def test_with_submissions(self):
        fee = compute_cancellation_fee(Decimal("100.00"), "open", True)
        assert fee == Decimal("25.00")

    def test_draft_no_submissions(self):
        fee = compute_cancellation_fee(Decimal("200.00"), "draft", False)
        assert fee == Decimal("20.00")

    def test_cannot_cancel_submitted(self):
        with pytest.raises(InvalidTransition):
            compute_cancellation_fee(Decimal("100.00"), "submitted", False)

    def test_cannot_cancel_settled(self):
        with pytest.raises(InvalidTransition):
            compute_cancellation_fee(Decimal("100.00"), "settled", False)

    def test_exact_arithmetic(self):
        fee = compute_cancellation_fee(Decimal("33.33"), "open", False)
        assert fee == Decimal("33.33") * Decimal("0.10")


class TestPayoutSplit:
    def test_basic_split(self):
        split = compute_payout_split(Decimal("100.00"))
        assert split["platform"] == Decimal("5.00")
        assert split["architect"] == Decimal("65.00")
        assert split["originator_pool"] == Decimal("30.00")

    def test_conservation(self):
        """Payout conservation invariant: sum of all splits == escrow."""
        for amount in [Decimal("100.00"), Decimal("33.33"), Decimal("1000.00"), Decimal("0.01")]:
            split = compute_payout_split(amount)
            total = split["platform"] + split["architect"] + split["originator_pool"]
            assert total == amount, f"Conservation violated for {amount}: {total}"

    def test_ratios(self):
        split = compute_payout_split(Decimal("1000.00"))
        assert split["platform"] == Decimal("50.00")
        assert split["architect"] == Decimal("650.00")
        assert split["originator_pool"] == Decimal("300.00")


class TestStateConsistency:
    def test_all_target_statuses_are_valid(self):
        for target in TARGET_STATUS.values():
            assert target in ALL_STATUSES

    def test_all_transition_statuses_are_valid(self):
        for current, target in VALID_TRANSITIONS:
            assert current in ALL_STATUSES
            assert target in ALL_STATUSES
