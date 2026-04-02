from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sciona_infra.api import policy
from sciona_infra.api.models import BountyCreateRequest, SubmissionRequest
from sciona_infra.api.routers import bounty
from sciona_infra.workflows import bounty_activities
from sciona_infra.workflows.bounty_activities import ComputeSettlementInput, compute_settlement

POLICY_DIR = Path(__file__).resolve().parent.parent / "docker" / "opa" / "policies"


def _opa_available() -> bool:
    return shutil.which("opa") is not None


def opa_eval(package: str, rule: str, input_data: dict[str, Any]) -> Any:
    result = subprocess.run(
        [
            "opa",
            "eval",
            "--data",
            str(POLICY_DIR),
            "--stdin-input",
            "--format",
            "json",
            f"data.{package}.{rule}",
        ],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    parsed = json.loads(result.stdout)
    return parsed["result"][0]["expressions"][0]["value"]


def _user(
    *,
    user_id: str = "user-1",
    identity_tier: str = "payee",
    effective_tier: str = "general",
    is_blacklisted: bool = False,
    reputation_score: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        identity_tier=identity_tier,
        effective_tier=effective_tier,
        is_blacklisted=is_blacklisted,
        reputation_score=reputation_score,
    )


def _bounty(
    *,
    bounty_id: str = "bounty-1",
    principal_id: str = "user-1",
    status: str = "draft",
    escrow_amount: float = 1000.0,
    tier: str = "standard",
) -> dict[str, Any]:
    return {
        "bounty_id": bounty_id,
        "principal_id": principal_id,
        "status": status,
        "escrow_amount": escrow_amount,
        "tier": tier,
    }


@pytest.mark.parametrize(
    "relative_path",
    [
        "data.json",
        "bounty.rego",
        "submission.rego",
        "payout.rego",
    ],
)
def test_policy_files_exist(relative_path: str) -> None:
    assert (POLICY_DIR / relative_path).exists()


@pytest.mark.skipif(not _opa_available(), reason="opa CLI not installed")
class TestRegoPolicies:
    def test_bounty_allow_create(self) -> None:
        assert opa_eval("bounty", "allow_create", {"user": _user().__dict__}) is True
        assert opa_eval("bounty", "allow_create", {"user": _user(identity_tier="contributor").__dict__}) is False
        assert opa_eval("bounty", "allow_create", {"user": _user(is_blacklisted=True).__dict__}) is False

    def test_bounty_fund_submit_cancel_target(self) -> None:
        open_bounty = _bounty(status="open")
        draft_bounty = _bounty(status="draft")
        submitted_bounty = _bounty(status="submitted")

        assert opa_eval("bounty", "allow_fund", {"user": _user().__dict__, "bounty": draft_bounty}) is True
        assert opa_eval("bounty", "allow_fund", {"user": _user(user_id="user-2").__dict__, "bounty": draft_bounty}) is False
        assert opa_eval("bounty", "allow_submit", {"user": _user(user_id="user-2").__dict__, "bounty": open_bounty}) is True
        assert opa_eval("bounty", "allow_submit", {"user": _user(user_id="user-1").__dict__, "bounty": open_bounty}) is False
        assert opa_eval("bounty", "allow_cancel", {"user": _user().__dict__, "bounty": draft_bounty}) is True
        assert opa_eval("bounty", "allow_update_target", {"user": _user().__dict__, "bounty": submitted_bounty}) is True

    def test_submission_allow_and_receipt(self) -> None:
        payload = {
            "user": _user(user_id="user-2").__dict__,
            "bounty": _bounty(status="open"),
            "submission": {"receipt_json": {"proof": "ok"}, "receipt_s3": ""},
        }
        assert opa_eval("submission", "allow", payload) is True
        assert opa_eval(
            "submission",
            "valid_receipt",
            {
                "user": _user(user_id="user-2").__dict__,
                "bounty": _bounty(status="open"),
                "submission": {"receipt_json": {}, "receipt_s3": ""},
            },
        ) is False

    def test_payout_rules(self) -> None:
        plan_input = {
            "plan": {
                "escrow_amount": 1000.0,
                "recipients": [
                    {"role": "platform", "amount": 50.0, "stripe_account_id": "platform"},
                    {"role": "architect", "amount": 650.0, "stripe_account_id": "acct_arch"},
                    {"role": "originator", "amount": 300.0, "stripe_account_id": "acct_orig"},
                ],
            }
        }
        assert opa_eval("payout", "valid_plan", plan_input) is True
        assert opa_eval("payout", "valid_split_percentages", plan_input) is True
        assert opa_eval(
            "payout",
            "valid_cancellation_fee",
            {
                "escrow_amount": 1000.0,
                "has_submissions": False,
                "cancellation_fee": 100.0,
            },
        ) is True
        assert opa_eval(
            "payout",
            "all_recipients_have_accounts",
            plan_input,
        ) is True


class TestPolicyClient:
    @pytest.mark.asyncio
    async def test_evaluate_policy_allowed(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": True}

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            result = await policy.evaluate_policy("bounty", "allow_create", {"user": {}})

        assert result is True

    @pytest.mark.asyncio
    async def test_evaluate_policy_denied(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": False}

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            result = await policy.evaluate_policy("bounty", "allow_create", {"user": {}})

        assert result is False

    @pytest.mark.asyncio
    async def test_require_policy_raises_with_reasons(self) -> None:
        with patch.object(
            policy,
            "evaluate_policy_with_reasons",
            AsyncMock(return_value=(False, ["not allowed"])),
        ):
            with pytest.raises(policy.PolicyDenied) as exc_info:
                await policy.require_policy("bounty", "allow_create", {})

        assert "not allowed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_permissive_fallback_allows_on_timeout(self) -> None:
        import httpx

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            with patch.object(policy, "_OPA_MODE", "permissive"):
                result = await policy.evaluate_policy("bounty", "allow_create", {})

        assert result is True

    @pytest.mark.asyncio
    async def test_strict_fallback_denies_on_timeout(self) -> None:
        import httpx

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client

            with patch.object(policy, "_OPA_MODE", "strict"):
                result = await policy.evaluate_policy("bounty", "allow_create", {})

        assert result is False


class TestRouterPolicyHooks:
    @pytest.mark.asyncio
    async def test_create_bounty_calls_bounty_policy(self) -> None:
        created_row = {
            "bounty_id": "44444444-4444-4444-4444-444444444444",
            "principal_id": "11111111-1111-1111-1111-111111111111",
            "title": "Create bounty",
            "escrow_amount": 25.0,
            "status": "draft",
            "deadline": None,
            "tier": "standard",
            "verification_budget": 5,
            "verifications_used": 0,
            "created_at": "2026-03-31T00:00:00Z",
            "updated_at": "2026-03-31T00:00:00Z",
            "config_yml": {},
            "flare_payload": None,
        }

        class FakeQuery:
            def __init__(self, name: str) -> None:
                self.name = name
                self.action = "select"
                self.payload = None

            def insert(self, payload: Any):
                self.action = "insert"
                self.payload = payload
                return self

            async def execute(self):
                return FakeResult([created_row])

        class FakeResult:
            def __init__(self, data: Any) -> None:
                self.data = data

        class FakeSupabase:
            def table(self, name: str) -> FakeQuery:
                return FakeQuery(name)

        with patch.object(bounty, "require_policy", AsyncMock()) as mock_require_policy:
            result = await bounty.create_bounty(
                BountyCreateRequest(title="Create bounty", escrow_amount=25.0),
                user=_user(),
                temporal=None,
                supabase=FakeSupabase(),
            )

        assert str(result.bounty_id) == created_row["bounty_id"]
        mock_require_policy.assert_awaited()
        assert mock_require_policy.await_args_list[0].args[:2] == ("bounty", "allow_create")

    @pytest.mark.asyncio
    async def test_submit_bounty_calls_submission_policy(self) -> None:
        bounty_row = {
            "bounty_id": "33333333-3333-3333-3333-333333333333",
            "principal_id": "11111111-1111-1111-1111-111111111111",
            "title": "Submit bounty",
            "escrow_amount": 25.0,
            "status": "open",
            "deadline": None,
            "tier": "standard",
            "verification_budget": 5,
            "verifications_used": 0,
            "created_at": "2026-03-31T00:00:00Z",
            "updated_at": "2026-03-31T00:00:00Z",
            "config_yml": {},
            "flare_payload": None,
        }

        class FakeQuery:
            def __init__(self, name: str) -> None:
                self.name = name
                self.action = "select"
                self.payload = None

            def select(self, *_args, **_kwargs):
                return self

            def insert(self, payload: Any):
                self.action = "insert"
                self.payload = payload
                return self

            def update(self, payload: Any):
                self.action = "update"
                self.payload = payload
                return self

            def eq(self, *_args, **_kwargs):
                return self

            def maybe_single(self):
                return self

            async def execute(self):
                if self.name == "bounties":
                    return FakeResult([bounty_row])
                if self.name == "submissions" and self.action == "insert":
                    return FakeResult(
                        [
                            {
                                "submission_id": "55555555-5555-5555-5555-555555555555",
                                "bounty_id": bounty_row["bounty_id"],
                            }
                        ]
                    )
                if self.name == "submissions":
                    return FakeResult(
                        [
                            {
                                "submission_id": "55555555-5555-5555-5555-555555555555",
                                "bounty_id": bounty_row["bounty_id"],
                                "verification_status": "pending",
                                "submitted_at": "2026-03-31T00:00:00Z",
                            }
                        ]
                    )
                return FakeResult([])

        class FakeResult:
            def __init__(self, data: Any) -> None:
                self.data = data

        class FakeSupabase:
            def table(self, name: str) -> FakeQuery:
                return FakeQuery(name)

        class FakeTemporal:
            async def start_workflow(self, *args, **kwargs):
                return None

            def get_workflow_handle(self, *_args, **_kwargs):
                class Handle:
                    async def signal(self, *_args, **_kwargs):
                        return None

                return Handle()

        with patch.object(bounty, "require_policy", AsyncMock()) as mock_require_policy:
            result = await bounty.submit_to_bounty(
                bounty_id=bounty_row["bounty_id"],
                body=SubmissionRequest(
                    cdg_hash="hash-1",
                    atom_versions={},
                    receipt_json={"proof": "ok"},
                    claimed_metric_name="loss",
                    claimed_metric_value=0.1,
                ),
                user=_user(user_id="user-2"),
                temporal=FakeTemporal(),
                supabase=FakeSupabase(),
            )

        assert str(result.bounty_id) == bounty_row["bounty_id"]
        assert [call.args[:2] for call in mock_require_policy.await_args_list] == [
            ("bounty", "allow_submit"),
            ("submission", "allow"),
        ]

    @pytest.mark.asyncio
    async def test_compute_settlement_validates_via_opa(self) -> None:
        class FakeQuery:
            def __init__(self, name: str) -> None:
                self.name = name
                self.action = "select"

            def select(self, *_args, **_kwargs):
                return self

            def in_(self, *_args, **_kwargs):
                return self

            def eq(self, *_args, **_kwargs):
                return self

            def maybe_single(self):
                return self

            async def execute(self):
                if self.name == "bounties":
                    return SimpleNamespace(data=[{"escrow_amount": 1000.0}])
                if self.name == "submissions":
                    return SimpleNamespace(
                        data=[
                            {
                                "submission_id": "sub-1",
                                "architect_id": "arch-1",
                                "cdg_hash": "cdg-1",
                                "atom_versions": {},
                                "metric_values": {},
                                "weight": 1.0,
                            }
                        ]
                    )
                return SimpleNamespace(data=[])

        class FakeSupabase:
            def table(self, name: str) -> FakeQuery:
                return FakeQuery(name)

        with patch.object(bounty_activities, "_get_supabase", AsyncMock(return_value=FakeSupabase())):
            with patch.object(bounty_activities, "evaluate_policy", AsyncMock(return_value=True)) as mock_evaluate:
                plan_json = await compute_settlement(
                    ComputeSettlementInput(
                        bounty_id="bounty-1",
                        escrow_amount=1000.0,
                        verified_submission_ids=["sub-1"],
                    )
                )

        assert str(json.loads(plan_json)["escrow_amount"]) == "1000.0"
        mock_evaluate.assert_awaited_once()
        assert mock_evaluate.await_args.args[:2] == ("payout", "valid_plan")
