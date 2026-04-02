from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID

import pytest

from sciona_infra.api.models import BountyCreateRequest
from sciona_infra.api.routers import bounty, verification
from sciona_infra.workflows import (
    BountyWorkflow,
    BountyWorkflowInput,
    RecordFundingInput,
)
from sciona_infra.workflows import bounty_activities
from sciona_infra.workflows.bounty_activities import compute_settlement, record_funding


@dataclass
class FakeResult:
    data: Any = None
    count: int | None = None


class FakeQuery:
    def __init__(self, client: "FakeSupabaseClient", name: str) -> None:
        self.client = client
        self.name = name
        self.action = "select"
        self.count: str | None = None
        self.payload: Any = None
        self.filters: list[tuple[str, str, Any]] = []

    def select(self, _fields: str, count: str | None = None):
        self.action = "select"
        self.count = count
        return self

    def insert(self, payload: Any):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append(("eq", field, value))
        return self

    def maybe_single(self):
        return self

    def order(self, _field: str, desc: bool = False):
        return self

    async def execute(self) -> FakeResult:
        return self.client.handler(self)


class FakeSupabaseClient:
    def __init__(self, handler: Callable[[FakeQuery], FakeResult]) -> None:
        self.handler = handler

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)


class FakeTemporalHandle:
    def __init__(self, client: "FakeTemporalClient", workflow_id: str) -> None:
        self.client = client
        self.workflow_id = workflow_id

    async def signal(self, signal, **payload) -> None:
        self.client.signal_calls.append(
            (self.workflow_id, signal.__name__, payload)
        )

    async def query(self, query):
        self.client.query_calls.append((self.workflow_id, query.__name__))
        return self.client.query_result


class FakeTemporalClient:
    def __init__(self, query_result: str = "submitted") -> None:
        self.query_result = query_result
        self.started_workflows: list[tuple[str, Any, str, str]] = []
        self.signal_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.query_calls: list[tuple[str, str]] = []

    async def start_workflow(self, workflow, args, id: str, task_queue: str):
        self.started_workflows.append((workflow.__name__, args[0], id, task_queue))

    def get_workflow_handle(self, workflow_id: str) -> FakeTemporalHandle:
        return FakeTemporalHandle(self, workflow_id)


@pytest.mark.asyncio
async def test_workflow_fallback_tracks_state_and_settlement_ids() -> None:
    workflow = BountyWorkflow()
    run_task = asyncio.create_task(
        workflow.run(
            BountyWorkflowInput(
                bounty_id="bounty-1",
                escrow_amount=100.0,
                principal_id="principal-1",
                deadline_seconds=0,
            )
        )
    )
    await asyncio.sleep(0)

    await workflow.fund("pi_123")
    assert workflow.get_status() == "open"

    await workflow.submit("submission-1", "architect-1")
    assert workflow.get_status() == "submitted"
    assert workflow.get_submissions()[0]["submission_id"] == "submission-1"

    await workflow.verify_complete("submission-1", True, {"loss": 0.1})
    assert workflow.get_status() == "verified"
    assert workflow.get_verified_submissions()[0]["submission_id"] == "submission-1"

    await workflow.settle(["tr_1", "tr_2"])
    assert await run_task == "settled"
    assert workflow.get_status() == "settled"
    assert workflow.get_settlement_transfers() == ["tr_1", "tr_2"]


@pytest.mark.asyncio
async def test_workflow_expires_after_deadline_when_open() -> None:
    workflow = BountyWorkflow()
    run_task = asyncio.create_task(
        workflow.run(
            BountyWorkflowInput(
                bounty_id="bounty-expire",
                escrow_amount=50.0,
                principal_id="principal-1",
                deadline_seconds=1,
            )
        )
    )
    await asyncio.sleep(0)

    await workflow.fund("pi_deadline")
    assert workflow.get_status() == "open"

    assert await run_task == "expired"
    assert workflow.get_status() == "expired"


@pytest.mark.asyncio
async def test_record_funding_uses_state_transition_validation(monkeypatch) -> None:
    updates: list[dict[str, Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "bounties" and query.action == "select":
            return FakeResult(data=[{"bounty_id": "b-1", "status": "draft"}])
        if query.name == "bounties" and query.action == "update":
            updates.append(query.payload)
            return FakeResult(data=[])
        raise AssertionError((query.name, query.action))

    async def fake_get_supabase():
        return FakeSupabaseClient(handler)

    monkeypatch.setattr(bounty_activities, "_get_supabase", fake_get_supabase)

    await record_funding(
        RecordFundingInput(bounty_id="b-1", stripe_payment_id="pi_123")
    )

    assert updates == [{"status": "open"}]


@pytest.mark.asyncio
async def test_launch_verification_marks_submission_pending(monkeypatch) -> None:
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "verification_runs" and query.action == "insert":
            inserts.append(query.payload)
            return FakeResult(data=[{"id": "run-1"}])
        if query.name == "submissions" and query.action == "update":
            updates.append(query.payload)
            return FakeResult(data=[])
        raise AssertionError((query.name, query.action))

    async def fake_get_supabase():
        return FakeSupabaseClient(handler)

    monkeypatch.setattr(bounty_activities, "_get_supabase", fake_get_supabase)

    run_id = await bounty_activities.launch_verification(
        bounty_activities.LaunchVerificationInput(
            bounty_id="b-1",
            submission_id="submission-1",
        )
    )

    assert run_id == "run-1"
    assert inserts[0]["split_type"] == "public"
    assert updates == [{"verification_status": "pending"}]


@pytest.mark.asyncio
async def test_compute_settlement_degrades_without_supabase() -> None:
    plan_json = await compute_settlement(
        bounty_activities.ComputeSettlementInput(
            bounty_id="b-1",
            escrow_amount=Decimal("10.0"),
            verified_submission_ids=["submission-1"],
        )
    )

    plan = json.loads(plan_json)
    assert Decimal(str(plan["escrow_amount"])) == Decimal("10.0")
    assert plan.get("recipients", []) == []


@pytest.mark.asyncio
async def test_create_bounty_starts_temporal_workflow_when_available() -> None:
    bounty_id = UUID("44444444-4444-4444-4444-444444444444")
    created_row = {
        "bounty_id": bounty_id,
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
    temporal = FakeTemporalClient()

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "bounties" and query.action == "insert":
            return FakeResult(data=[created_row])
        raise AssertionError((query.name, query.action))

    client = FakeSupabaseClient(handler)
    body = BountyCreateRequest(title="Create bounty", escrow_amount=25.0)
    user = SimpleNamespace(user_id="11111111-1111-1111-1111-111111111111")

    result = await bounty.create_bounty(
        body,
        user=user,
        temporal=temporal,
        supabase=client,
    )

    assert result.bounty_id == bounty_id
    assert temporal.started_workflows[0][0] == "run"
    assert temporal.started_workflows[0][2] == "bounty-44444444-4444-4444-4444-444444444444"
    assert temporal.started_workflows[0][3] == "bounty-lifecycle"


@pytest.mark.asyncio
async def test_temporal_signal_and_query_paths_are_used_when_available() -> None:
    bounty_id = UUID("55555555-5555-5555-5555-555555555555")
    submission_id = UUID("66666666-6666-6666-6666-666666666666")
    temporal = FakeTemporalClient(query_result="verified")

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "bounties" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "bounty_id": bounty_id,
                        "principal_id": "22222222-2222-2222-2222-222222222222",
                        "title": "Fund bounty",
                        "escrow_amount": 10.0,
                        "status": "draft",
                        "deadline": None,
                        "tier": "standard",
                        "verification_budget": 5,
                        "verifications_used": 0,
                        "config_yml": {},
                        "created_at": "2026-03-31T00:00:00Z",
                        "updated_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        if query.name == "submissions" and query.action == "select":
            if query.count == "exact":
                return FakeResult(data=[{"submission_id": str(submission_id)}], count=1)
            return FakeResult(
                data=[
                    {
                        "submission_id": submission_id,
                        "bounty_id": bounty_id,
                        "verification_status": "pending",
                        "submitted_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        if query.name == "bounties" and query.action == "update":
            return FakeResult(data=[])
        if query.name == "verification_runs":
            return FakeResult(data=[])
        raise AssertionError((query.name, query.action))

    client = FakeSupabaseClient(handler)
    user = SimpleNamespace(user_id="22222222-2222-2222-2222-222222222222")

    fund_result = await bounty.fund_bounty(
        bounty_id,
        user=user,
        temporal=temporal,
        supabase=client,
    )
    assert fund_result.status == "open"
    assert temporal.signal_calls[0][1] == "fund"

    status_result = await verification.get_submission_status(
        submission_id,
        temporal=temporal,
        supabase=client,
    )
    assert status_result["verification_status"] == "verified"
    assert temporal.query_calls[0][1] == "get_status"


@pytest.mark.asyncio
async def test_submit_to_bounty_signals_temporal_workflow() -> None:
    bounty_id = UUID("77777777-7777-7777-7777-777777777777")
    submission_id = UUID("88888888-8888-8888-8888-888888888888")
    temporal = FakeTemporalClient()

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "bounties" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "bounty_id": bounty_id,
                        "principal_id": "33333333-3333-3333-3333-333333333333",
                        "title": "Submit bounty",
                        "escrow_amount": 10.0,
                        "status": "submitted",
                        "deadline": None,
                        "tier": "standard",
                        "verification_budget": 5,
                        "verifications_used": 0,
                        "config_yml": {},
                        "created_at": "2026-03-31T00:00:00Z",
                        "updated_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        if query.name == "submissions" and query.action == "insert":
            return FakeResult(data=[{"submission_id": submission_id}])
        if query.name == "submissions" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "submission_id": submission_id,
                        "bounty_id": bounty_id,
                        "verification_status": "pending",
                        "submitted_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        raise AssertionError((query.name, query.action))

    client = FakeSupabaseClient(handler)
    user = SimpleNamespace(user_id="33333333-3333-3333-3333-333333333333")
    body = bounty.SubmissionRequest(
        cdg_hash="cdg-hash",
        atom_versions={"pkg.mod.filter": "hash-1"},
        receipt_json={"bounty_id": str(bounty_id)},
        claimed_metric_name="loss",
        claimed_metric_value=0.25,
    )

    result = await bounty.submit_to_bounty(
        bounty_id,
        body,
        user=user,
        temporal=temporal,
        supabase=client,
    )

    assert result.submission_id == submission_id
    assert temporal.signal_calls[0][1] == "submit"
    assert temporal.signal_calls[0][2]["submission_id"] == str(submission_id)
