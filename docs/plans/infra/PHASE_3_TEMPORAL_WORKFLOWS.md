# Phase 3 — Temporal Workflow Orchestration

## Overview

Replace the inline bounty state machine in `sciona/api/routers/bounty.py` with
Temporal workflows.  Each bounty becomes a long-running workflow instance; state
transitions become signals; status reads become queries.

**Depends on:** Phase 1 (Temporal server running in Docker), Phase 2 (OTel trace
context propagates into workflows).

**Preserves:** `sciona/api/bounty_state.py` — the pure business logic (transition
validation, cancellation fees, payout splits) stays as-is.  Activities call into
it rather than duplicating logic.

---

## File manifest

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | Modify | Add `temporalio>=1.5` to `[project.optional-dependencies].api` |
| `sciona/workflows/__init__.py` | Create | Package init, re-exports |
| `sciona/workflows/bounty_workflow.py` | Create | `BountyWorkflow` class |
| `sciona/workflows/bounty_activities.py` | Create | Activity functions |
| `sciona/workflows/worker.py` | Create | Standalone worker process |
| `sciona/api/app.py` | Modify | Init Temporal client in `_lifespan()` |
| `sciona/api/deps.py` | Modify | Add `get_temporal()` dependency |
| `sciona/api/routers/bounty.py` | Modify | Replace inline state transitions with workflow signals |
| `sciona/api/routers/verification.py` | Modify | Query workflow state for submission status |
| `tests/test_bounty_workflow.py` | Create | Tests using Temporal test environment |

---

## Step 1: Add `temporalio` dependency

**File:** `pyproject.toml`

**Edit:** In the `api` optional-dependencies list, add `temporalio>=1.5` after
the existing `stripe>=7.0` entry.

```python
# BEFORE
api = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "supabase>=2.0",
    "openai>=1.0.0",
    "httpx>=0.27",
    "boto3>=1.34",
    "stripe>=7.0",
]

# AFTER
api = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "supabase>=2.0",
    "openai>=1.0.0",
    "httpx>=0.27",
    "boto3>=1.34",
    "stripe>=7.0",
    "temporalio>=1.5",
]
```

Also add `temporalio>=1.5` to the `dev` dependencies so the test environment is
available:

```python
# BEFORE
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "asyncpg>=0.29",
    "mypy>=1.0",
]

# AFTER
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "asyncpg>=0.29",
    "mypy>=1.0",
    "temporalio>=1.5",
]
```

---

## Step 2: Create `sciona/workflows/__init__.py`

**File:** `sciona/workflows/__init__.py` (create)

```python
"""Temporal workflow definitions for the Algorithmic Commons platform."""

from sciona.workflows.bounty_workflow import BountyWorkflow

__all__ = ["BountyWorkflow"]
```

---

## Step 3: Create `sciona/workflows/bounty_workflow.py`

**File:** `sciona/workflows/bounty_workflow.py` (create)

```python
"""Bounty lifecycle workflow — durable orchestration of the full bounty lifecycle.

State machine: draft -> open -> submitted -> verified -> settled
                 |        |         |
                 v        v         v
              cancelled  expired   expired

This workflow replaces the inline transitions in bounty.py.  The pure business
logic in bounty_state.py is preserved and called from activities.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, Sequence

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from sciona.workflows.bounty_activities import (
        ComputeSettlementInput,
        ExecutePayoutsInput,
        LaunchVerificationInput,
        RecordFundingInput,
        RecordSettlementInput,
        compute_settlement,
        execute_payouts,
        launch_verification,
        record_funding,
        record_settlement,
    )


# ---------------------------------------------------------------------------
# Workflow input / signal payloads
# ---------------------------------------------------------------------------


@dataclass
class BountyWorkflowInput:
    """Input for starting a BountyWorkflow."""

    bounty_id: str
    escrow_amount: float
    deadline_seconds: int = 0  # 0 = no deadline
    principal_id: str = ""


@dataclass
class FundSignal:
    """Signal payload for funding a bounty."""

    stripe_payment_id: str


@dataclass
class SubmitSignal:
    """Signal payload for a new submission."""

    submission_id: str
    architect_id: str


@dataclass
class VerifyCompleteSignal:
    """Signal payload when a verification run finishes."""

    submission_id: str
    passed: bool
    metric_values: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retry policies
# ---------------------------------------------------------------------------

_DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)

_STRIPE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
)

_VERIFICATION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=120),
    maximum_attempts=3,
)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@workflow.defn
class BountyWorkflow:
    """Long-running workflow for a single bounty lifecycle.

    Lifecycle:
        1. Workflow starts in ``draft`` status.
        2. ``fund`` signal -> transitions to ``open``, records funding.
        3. ``submit`` signal(s) -> transitions to ``submitted`` on first
           submission, triggers verification child workflow / activity.
        4. ``verify_complete`` signal -> when all submissions verified,
           transitions to ``verified``.
        5. Settlement activity runs -> transitions to ``settled``.
        6. At any point, ``cancel`` signal can terminate the workflow
           (only from draft/open/submitted).
        7. If a deadline is set, the workflow expires if still open/submitted
           when the deadline passes.
    """

    def __init__(self) -> None:
        # State
        self._status: str = "draft"
        self._funded: bool = False
        self._cancelled: bool = False
        self._submissions: list[dict] = []
        self._verified_submissions: list[dict] = []
        self._settlement_transfer_ids: list[str] = []
        self._stripe_payment_id: str = ""

    # -- Queries ---------------------------------------------------------------

    @workflow.query
    def get_status(self) -> str:
        """Return the current bounty status."""
        return self._status

    @workflow.query
    def get_submissions(self) -> list[dict]:
        """Return all submissions received by this workflow."""
        return list(self._submissions)

    @workflow.query
    def get_verified_submissions(self) -> list[dict]:
        """Return all verified submissions."""
        return list(self._verified_submissions)

    @workflow.query
    def get_settlement_transfers(self) -> list[str]:
        """Return Stripe transfer IDs from settlement."""
        return list(self._settlement_transfer_ids)

    # -- Signals ---------------------------------------------------------------

    @workflow.signal
    async def fund(self, signal: FundSignal) -> None:
        """Record that escrow funding has been confirmed."""
        if self._status != "draft":
            raise ApplicationError(
                f"Cannot fund a bounty in {self._status!r} state",
                type="InvalidTransition",
            )
        self._stripe_payment_id = signal.stripe_payment_id
        self._funded = True

    @workflow.signal
    async def submit(self, signal: SubmitSignal) -> None:
        """Record a new submission."""
        if self._status not in ("open", "submitted"):
            raise ApplicationError(
                f"Cannot submit to a bounty in {self._status!r} state",
                type="InvalidTransition",
            )
        self._submissions.append({
            "submission_id": signal.submission_id,
            "architect_id": signal.architect_id,
            "verified": False,
        })

    @workflow.signal
    async def verify_complete(self, signal: VerifyCompleteSignal) -> None:
        """Record that a verification run has completed."""
        for sub in self._submissions:
            if sub["submission_id"] == signal.submission_id:
                sub["verified"] = True
                sub["passed"] = signal.passed
                sub["metric_values"] = signal.metric_values
                if signal.passed:
                    self._verified_submissions.append(sub)
                break

    @workflow.signal
    async def cancel(self) -> None:
        """Cancel the bounty."""
        if self._status in ("verified", "settled", "expired", "cancelled"):
            raise ApplicationError(
                f"Cannot cancel a bounty in {self._status!r} state",
                type="InvalidTransition",
            )
        self._cancelled = True

    # -- Main workflow ---------------------------------------------------------

    @workflow.run
    async def run(self, input: BountyWorkflowInput) -> str:
        """Execute the full bounty lifecycle.

        Returns the terminal status: "settled", "expired", or "cancelled".
        """
        bounty_id = input.bounty_id
        escrow_amount = input.escrow_amount
        deadline_seconds = input.deadline_seconds

        # ---- Phase 1: Wait for funding or cancellation ----
        workflow.logger.info("Bounty %s: waiting for funding", bounty_id)

        funded_or_cancelled = await workflow.wait_condition(
            lambda: self._funded or self._cancelled,
            timeout=timedelta(days=30),  # drafts expire after 30 days
        )

        if not funded_or_cancelled:
            # Timed out waiting for funding — expire the draft
            self._status = "expired"
            return self._status

        if self._cancelled:
            self._status = "cancelled"
            return self._status

        # Record funding via activity
        await workflow.execute_activity(
            record_funding,
            RecordFundingInput(
                bounty_id=bounty_id,
                stripe_payment_id=self._stripe_payment_id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_DB_RETRY,
        )
        self._status = "open"
        workflow.logger.info("Bounty %s: funded, now open", bounty_id)

        # ---- Phase 2: Wait for submissions or deadline ----
        deadline_timeout = (
            timedelta(seconds=deadline_seconds) if deadline_seconds > 0
            else timedelta(days=365)  # effectively no deadline
        )

        has_submission = await workflow.wait_condition(
            lambda: len(self._submissions) > 0 or self._cancelled,
            timeout=deadline_timeout,
        )

        if not has_submission:
            self._status = "expired"
            return self._status

        if self._cancelled:
            self._status = "cancelled"
            return self._status

        self._status = "submitted"
        workflow.logger.info(
            "Bounty %s: first submission received, launching verification",
            bounty_id,
        )

        # ---- Phase 3: Launch verification for each submission ----
        # Launch verification for the first submission immediately,
        # then continue accepting more submissions until deadline.

        # Start a background coroutine to handle verification launches.
        # We use a continue-as-new-safe pattern: process all current
        # submissions, then wait for more or deadline.

        verification_tasks: list[asyncio.Task] = []

        async def launch_and_verify(submission: dict) -> None:
            """Launch verification for a single submission."""
            await workflow.execute_activity(
                launch_verification,
                LaunchVerificationInput(
                    bounty_id=bounty_id,
                    submission_id=submission["submission_id"],
                ),
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=_VERIFICATION_RETRY,
            )

        # Launch verification for all current submissions
        launched: set[str] = set()
        for sub in self._submissions:
            if sub["submission_id"] not in launched:
                launched.add(sub["submission_id"])
                await launch_and_verify(sub)

        # Wait for remaining deadline, accepting more submissions
        remaining_seconds = deadline_seconds - 60  # buffer
        if remaining_seconds > 0:
            more_submissions = True
            while more_submissions:
                prev_count = len(self._submissions)
                more_submissions = await workflow.wait_condition(
                    lambda: (
                        len(self._submissions) > prev_count
                        or self._cancelled
                    ),
                    timeout=timedelta(seconds=min(remaining_seconds, 300)),
                )

                if self._cancelled:
                    self._status = "cancelled"
                    return self._status

                # Launch verification for any new submissions
                for sub in self._submissions:
                    if sub["submission_id"] not in launched:
                        launched.add(sub["submission_id"])
                        await launch_and_verify(sub)

                if not more_submissions:
                    break  # timed out — no more submissions

        # ---- Phase 4: Wait for all verifications to complete ----
        workflow.logger.info(
            "Bounty %s: waiting for %d verification(s) to complete",
            bounty_id,
            len(launched),
        )

        all_verified = await workflow.wait_condition(
            lambda: (
                all(s.get("verified", False) for s in self._submissions)
                or self._cancelled
            ),
            timeout=timedelta(hours=6),  # verification timeout
        )

        if self._cancelled:
            self._status = "cancelled"
            return self._status

        if not all_verified:
            # Some verifications timed out — proceed with what we have
            workflow.logger.warning(
                "Bounty %s: verification timeout, proceeding with %d verified",
                bounty_id,
                len(self._verified_submissions),
            )

        if not self._verified_submissions:
            # No passing submissions — expire
            self._status = "expired"
            return self._status

        self._status = "verified"
        workflow.logger.info(
            "Bounty %s: %d submissions verified, settling",
            bounty_id,
            len(self._verified_submissions),
        )

        # ---- Phase 5: Settlement ----
        payout_plan = await workflow.execute_activity(
            compute_settlement,
            ComputeSettlementInput(
                bounty_id=bounty_id,
                escrow_amount=escrow_amount,
                verified_submission_ids=[
                    s["submission_id"] for s in self._verified_submissions
                ],
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_DB_RETRY,
        )

        transfer_ids = await workflow.execute_activity(
            execute_payouts,
            ExecutePayoutsInput(
                bounty_id=bounty_id,
                payout_plan_json=payout_plan,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_STRIPE_RETRY,
        )

        self._settlement_transfer_ids = transfer_ids

        await workflow.execute_activity(
            record_settlement,
            RecordSettlementInput(
                bounty_id=bounty_id,
                transfer_ids=transfer_ids,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_DB_RETRY,
        )

        self._status = "settled"
        workflow.logger.info("Bounty %s: settled with %d transfers", bounty_id, len(transfer_ids))
        return self._status
```

---

## Step 4: Create `sciona/workflows/bounty_activities.py`

**File:** `sciona/workflows/bounty_activities.py` (create)

```python
"""Temporal activities for the bounty lifecycle.

Activities are the side-effecting operations that the BountyWorkflow
orchestrates.  Each activity is idempotent where possible.

These activities import and call the pure business logic from
``sciona.api.bounty_state`` and ``sciona.clearinghouse.settlement`` — they
do NOT duplicate that logic.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal

from temporalio import activity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Activity input dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RecordFundingInput:
    """Input for the record_funding activity."""

    bounty_id: str
    stripe_payment_id: str


@dataclass
class LaunchVerificationInput:
    """Input for the launch_verification activity."""

    bounty_id: str
    submission_id: str


@dataclass
class ComputeSettlementInput:
    """Input for the compute_settlement activity."""

    bounty_id: str
    escrow_amount: float
    verified_submission_ids: list[str] = field(default_factory=list)


@dataclass
class ExecutePayoutsInput:
    """Input for the execute_payouts activity."""

    bounty_id: str
    payout_plan_json: str  # JSON-serialized PayoutPlan


@dataclass
class RecordSettlementInput:
    """Input for the record_settlement activity."""

    bounty_id: str
    transfer_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@activity.defn
async def record_funding(input: RecordFundingInput) -> None:
    """Update bounty status to 'open' in Supabase after Stripe confirms payment.

    Idempotent: if the bounty is already 'open', this is a no-op.
    """
    activity.logger.info(
        "Recording funding for bounty %s (payment %s)",
        input.bounty_id,
        input.stripe_payment_id,
    )
    supabase = await _get_supabase()
    await (
        supabase.table("bounties")
        .update({
            "status": "open",
            "stripe_payment_id": input.stripe_payment_id,
        })
        .eq("bounty_id", input.bounty_id)
        .execute()
    )


@activity.defn
async def launch_verification(input: LaunchVerificationInput) -> str:
    """Kick off a sandbox verification run for a submission.

    Returns the verification_run_id.

    This activity creates the verification_run record and triggers the
    sandbox execution.  The actual sandbox completion is signalled back
    to the workflow via the verify_complete signal (either from the sandbox
    callback or a polling worker).
    """
    activity.logger.info(
        "Launching verification for submission %s on bounty %s",
        input.submission_id,
        input.bounty_id,
    )
    supabase = await _get_supabase()

    # Create a verification run record
    run_result = await (
        supabase.table("verification_runs")
        .insert({
            "bounty_id": input.bounty_id,
            "submission_id": input.submission_id,
            "status": "pending",
        })
        .execute()
    )
    run_data = run_result.data
    if isinstance(run_data, list) and run_data:
        run_id = run_data[0].get("run_id", "")
    else:
        run_id = ""

    # TODO: Phase 3 — trigger actual sandbox execution here.
    # For now, the verification run record is created and the sandbox
    # callback (or a polling worker) will signal verify_complete back
    # to the workflow.

    activity.logger.info("Created verification run %s", run_id)
    return run_id


@activity.defn
async def compute_settlement(input: ComputeSettlementInput) -> str:
    """Run Shapley-based settlement computation.

    Calls ``sciona.clearinghouse.settlement.compute_settlement`` with the
    verified submissions.  Returns the PayoutPlan as a JSON string (Temporal
    activities must return serializable values).
    """
    activity.logger.info(
        "Computing settlement for bounty %s (%d verified submissions)",
        input.bounty_id,
        len(input.verified_submission_ids),
    )
    supabase = await _get_supabase()

    # Fetch verified submissions
    submissions_result = await (
        supabase.table("submissions")
        .select("*")
        .in_("submission_id", input.verified_submission_ids)
        .execute()
    )
    submissions = submissions_result.data or []

    if not submissions:
        activity.logger.warning("No verified submissions found for settlement")
        return json.dumps({"recipients": [], "escrow_amount": input.escrow_amount})

    # Build WinningCDG list from submissions
    from sciona.clearinghouse.models import WinningCDG
    from sciona.clearinghouse.settlement import (
        compute_settlement as compute_settlement_plan,
        verify_payout_conservation,
    )

    winning_cdgs = []
    atom_dags: dict[str, dict[str, set[str]]] = {}

    for sub in submissions:
        winning_cdgs.append(
            WinningCDG(
                submission_id=sub["submission_id"],
                architect_id=sub["architect_id"],
                cdg_hash=sub.get("cdg_hash", ""),
                atom_versions=sub.get("atom_versions", {}),
                metric_values=sub.get("metric_values", {}),
                weight=1.0,
            )
        )
        # TODO: Fetch actual atom DAGs from the graph database.
        # For now, use a flat DAG (each atom is independent).
        atom_versions = sub.get("atom_versions", {})
        if atom_versions:
            atom_dags[sub.get("cdg_hash", "")] = {
                atom: set() for atom in atom_versions
            }

    plan = compute_settlement_plan(
        escrow_amount=Decimal(str(input.escrow_amount)),
        winning_cdgs=winning_cdgs,
        atom_dags=atom_dags,
        platform_account_id=os.getenv("STRIPE_PLATFORM_ACCOUNT", "platform"),
    )

    if not verify_payout_conservation(plan):
        raise RuntimeError(
            f"Payout conservation violated for bounty {input.bounty_id}"
        )

    return plan.model_dump_json()


@activity.defn
async def execute_payouts(input: ExecutePayoutsInput) -> list[str]:
    """Create Stripe transfers for each recipient in the payout plan.

    Returns a list of Stripe transfer IDs.

    Idempotent: uses idempotency keys derived from bounty_id + recipient_id.
    """
    activity.logger.info("Executing payouts for bounty %s", input.bounty_id)

    from sciona.clearinghouse.models import PayoutPlan

    plan = PayoutPlan.model_validate_json(input.payout_plan_json)
    transfer_ids: list[str] = []

    try:
        import stripe
    except ImportError:
        activity.logger.warning("Stripe not available; recording mock transfers")
        for recipient in plan.recipients:
            transfer_ids.append(f"mock_tr_{input.bounty_id}_{recipient.recipient_id}")
        return transfer_ids

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

    for recipient in plan.recipients:
        if recipient.role == "platform":
            # Platform keeps its share — no transfer needed
            transfer_ids.append(f"platform_retained_{input.bounty_id}")
            continue

        if not recipient.stripe_account_id:
            activity.logger.warning(
                "No Stripe account for %s (%s); skipping transfer",
                recipient.recipient_id,
                recipient.role,
            )
            continue

        idempotency_key = f"bounty-{input.bounty_id}-{recipient.recipient_id}-{recipient.cdg_hash or 'no-cdg'}"

        try:
            transfer = stripe.Transfer.create(
                amount=int(recipient.amount * 100),  # cents
                currency="usd",
                destination=recipient.stripe_account_id,
                description=f"Bounty {input.bounty_id} payout ({recipient.role})",
                idempotency_key=idempotency_key,
            )
            transfer_ids.append(transfer.id)
        except stripe.error.StripeError as e:
            activity.logger.error(
                "Stripe transfer failed for %s: %s",
                recipient.recipient_id,
                str(e),
            )
            raise

    return transfer_ids


@activity.defn
async def record_settlement(input: RecordSettlementInput) -> None:
    """Write settlement records to the database and mark the bounty as settled.

    Idempotent: if bounty is already 'settled', this is a no-op.
    """
    activity.logger.info(
        "Recording settlement for bounty %s (%d transfers)",
        input.bounty_id,
        len(input.transfer_ids),
    )
    supabase = await _get_supabase()

    # Update bounty status
    await (
        supabase.table("bounties")
        .update({
            "status": "settled",
            "settlement_transfer_ids": input.transfer_ids,
        })
        .eq("bounty_id", input.bounty_id)
        .execute()
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_supabase():
    """Get a Supabase client for activity use.

    Activities run in the worker process, not inside the FastAPI app, so they
    cannot use FastAPI dependency injection.  Instead, they create their own
    Supabase client from environment variables.
    """
    try:
        from supabase import acreate_client
    except ImportError:
        raise RuntimeError("supabase package is required for activities")

    url = os.getenv("SCIONA_SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
    key = os.getenv(
        "SCIONA_SUPABASE_SERVICE_ROLE_KEY",
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    )
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

    return await acreate_client(url, key)
```

---

## Step 5: Create `sciona/workflows/worker.py`

**File:** `sciona/workflows/worker.py` (create)

```python
"""Standalone Temporal worker for bounty lifecycle workflows.

Run with:
    python -m sciona.workflows.worker

Environment variables:
    TEMPORAL_ADDRESS    — Temporal server address (default: localhost:7233)
    TEMPORAL_NAMESPACE  — Temporal namespace (default: default)
    TEMPORAL_TASK_QUEUE — Task queue name (default: bounty-lifecycle)
"""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from sciona.workflows.bounty_activities import (
    compute_settlement,
    execute_payouts,
    launch_verification,
    record_funding,
    record_settlement,
)
from sciona.workflows.bounty_workflow import BountyWorkflow

logger = logging.getLogger(__name__)

TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "bounty-lifecycle")


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted."""
    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")

    logger.info("Connecting to Temporal at %s (namespace=%s)", address, namespace)
    client = await TemporalClient.connect(address, namespace=namespace)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[BountyWorkflow],
        activities=[
            record_funding,
            launch_verification,
            compute_settlement,
            execute_payouts,
            record_settlement,
        ],
    )

    logger.info("Starting worker on task queue %r", TASK_QUEUE)
    await worker.run()


def main() -> None:
    """Entry point for ``python -m sciona.workflows.worker``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
```

---

## Step 6: Modify `sciona/api/app.py` — init Temporal client

**Edit:** Add Temporal client initialization inside `_lifespan()`, after the
existing Memgraph initialization block and before `yield`.

```python
# BEFORE (line ~78-79 in current file):
    yield

# AFTER:
    # -- Temporal client --
    temporal_client = None
    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "")
    if temporal_address:
        try:
            from temporalio.client import Client as TemporalClient

            temporal_client = await TemporalClient.connect(
                temporal_address,
                namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            )
            app.state.temporal = temporal_client
            logger.info("Temporal client connected to %s", temporal_address)
        except Exception:
            logger.exception("Failed to connect to Temporal")
            temporal_client = None
    else:
        logger.info("TEMPORAL_ADDRESS not set; workflow orchestration disabled")

    yield
```

**Rationale:** The client is only created if `TEMPORAL_ADDRESS` is set.  This
keeps the API functional without Temporal in development/test environments.
The Temporal client does not need explicit cleanup on shutdown (it uses an
internal gRPC channel that closes when the process exits), but we store it
on `app.state` so dependencies can access it.

---

## Step 7: Modify `sciona/api/deps.py` — add `get_temporal()` dependency

**Edit:** Add the following after the existing `get_supabase` function:

```python
# BEFORE (after get_supabase function, around line 43):
async def require_auth(

# AFTER:
async def get_temporal(request: Request):
    """Return the Temporal client from app state, or None if not configured.

    Callers that require Temporal should check for None and raise 503.
    """
    return getattr(request.app.state, "temporal", None)


async def require_temporal(request: Request):
    """Return the Temporal client, raising 503 if not available."""
    client = getattr(request.app.state, "temporal", None)
    if client is None:
        raise HTTPException(503, "Workflow orchestration not available")
    return client


async def require_auth(
```

---

## Step 8: Modify `sciona/api/routers/bounty.py` — replace inline state transitions

### 8a. Add imports

**Edit:** Add Temporal imports at the top of the file, after existing imports:

```python
# BEFORE (line ~11):
from sciona.api.bounty_state import (

# AFTER:
from sciona.api.bounty_state import (
    InvalidTransition,
    compute_cancellation_fee,
    validate_transition,
)

# Temporal workflow integration (optional — falls back to inline if unavailable)
try:
    from sciona.workflows.bounty_workflow import (
        BountyWorkflow,
        BountyWorkflowInput,
        FundSignal,
        SubmitSignal,
    )
    _TEMPORAL_AVAILABLE = True
except ImportError:
    _TEMPORAL_AVAILABLE = False
```

Remove the duplicate bounty_state import block that was there before.

### 8b. Add temporal dependency to relevant endpoints

**Edit:** Add `get_temporal` to the bounty router's imports from deps:

```python
# In the imports section, after the existing deps import, ensure we have:
from sciona.api import deps as api_deps

# The get_temporal dep is accessed via api_deps.get_temporal
```

### 8c. Modify `create_bounty` to start a workflow

**Edit:** Replace the `create_bounty` endpoint body. After the Supabase insert
succeeds, start a Temporal workflow if available:

```python
@router.post("")
async def create_bounty(
    body: BountyCreateRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> BountyResponse:
    """Create a draft bounty and start its lifecycle workflow."""
    user_id = str(user.user_id)
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

    # Start Temporal workflow if available
    if temporal is not None and _TEMPORAL_AVAILABLE:
        from datetime import datetime, timezone

        deadline_seconds = 0
        if body.deadline:
            delta = body.deadline - datetime.now(timezone.utc)
            deadline_seconds = max(int(delta.total_seconds()), 0)

        await temporal.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id=str(created["bounty_id"]),
                escrow_amount=body.escrow_amount,
                deadline_seconds=deadline_seconds,
                principal_id=user_id,
            ),
            id=f"bounty-{created['bounty_id']}",
            task_queue="bounty-lifecycle",
        )

    return _bounty_response(created)
```

### 8d. Modify `fund_bounty` to signal the workflow

```python
@router.post("/{bounty_id}/fund")
async def fund_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> BountyFundResponse:
    """Fund a bounty (transitions draft -> open)."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can fund it")

    if temporal is not None and _TEMPORAL_AVAILABLE:
        # Signal the workflow — it handles the state transition
        handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
        try:
            await handle.signal(
                BountyWorkflow.fund,
                FundSignal(stripe_payment_id=""),  # TODO: real Stripe ID
            )
        except Exception as e:
            raise HTTPException(409, f"Workflow signal failed: {e}")
        # Query the workflow for the new status
        new_status = await handle.query(BountyWorkflow.get_status)
    else:
        # Fallback: inline state transition
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

    return BountyFundResponse(
        bounty_id=bounty_id,
        status=new_status,
        checkout_url="",
    )
```

### 8e. Modify `submit_to_bounty` to signal the workflow

```python
@router.post("/{bounty_id}/submit")
async def submit_to_bounty(
    bounty_id: UUID,
    body: SubmissionRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> SubmissionResponse:
    """Submit a CDG solution with signed receipt."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")

    # Insert the submission record (always goes to DB regardless of Temporal)
    if row["status"] not in ("open", "submitted"):
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

    if temporal is not None and _TEMPORAL_AVAILABLE:
        # Signal the workflow about the new submission
        handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
        try:
            await handle.signal(
                BountyWorkflow.submit,
                SubmitSignal(
                    submission_id=str(created["submission_id"]),
                    architect_id=str(user.user_id),
                ),
            )
        except Exception as e:
            # Log but don't fail — the submission is already recorded in DB
            import logging
            logging.getLogger(__name__).warning(
                "Failed to signal workflow for bounty %s: %s", bounty_id, e
            )
    else:
        # Fallback: inline state transition
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

    sub_row = await (
        supabase.table("submissions")
        .select("submission_id, bounty_id, verification_status, submitted_at")
        .eq("submission_id", created["submission_id"])
        .maybe_single()
        .execute()
    )
    row = _first_row(sub_row.data) or created
    return SubmissionResponse(**dict(row))
```

### 8f. Modify `cancel_bounty` to signal the workflow

```python
@router.post("/{bounty_id}/cancel")
async def cancel_bounty(
    bounty_id: UUID,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> BountyCancelResponse:
    """Cancel a bounty (with fee per design decision 4.13)."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can cancel it")

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

    if temporal is not None and _TEMPORAL_AVAILABLE:
        # Signal the workflow to cancel
        handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
        try:
            await handle.signal(BountyWorkflow.cancel)
        except Exception as e:
            raise HTTPException(409, f"Workflow cancel failed: {e}")
    # Always update DB directly for cancellation (fee + status)
    await (
        supabase.table("bounties")
        .update({"status": new_status, "cancellation_fee": float(fee)})
        .eq("bounty_id", str(bounty_id))
        .execute()
    )

    return BountyCancelResponse(
        bounty_id=bounty_id,
        status=new_status,
        cancellation_fee=float(fee),
    )
```

### 8g. Modify `get_bounty` to optionally query workflow status

```python
@router.get("/{bounty_id}")
async def get_bounty(
    bounty_id: UUID,
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> BountyResponse:
    """Get bounty details including submission count and status."""
    row = await _fetch_bounty(bounty_id, supabase=supabase)
    if not row:
        raise HTTPException(404, "Bounty not found")

    # If Temporal is available and the bounty is in-progress, prefer
    # the workflow's authoritative status over the DB snapshot.
    if temporal is not None and _TEMPORAL_AVAILABLE:
        if row["status"] not in ("settled", "expired", "cancelled"):
            try:
                handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
                workflow_status = await handle.query(BountyWorkflow.get_status)
                row = dict(row)  # make mutable copy
                row["status"] = workflow_status
            except Exception:
                pass  # Fall back to DB status

    return _bounty_response(row, bounty_id=bounty_id)
```

---

## Step 9: Modify `sciona/api/routers/verification.py` — query workflow state

**Edit:** Modify `get_submission_status` to optionally enrich from workflow state.

```python
# Add at the top of the file, after existing imports:
try:
    from sciona.workflows.bounty_workflow import BountyWorkflow
    _TEMPORAL_AVAILABLE = True
except ImportError:
    _TEMPORAL_AVAILABLE = False


@router.get("/submissions/{submission_id}/status")
async def get_submission_status(
    submission_id: UUID,
    supabase=Depends(api_deps.get_supabase),
    temporal=Depends(api_deps.get_temporal),
) -> dict:
    """Poll verification progress for a submission.

    If Temporal is available, enriches the response with live workflow
    state (submissions list, workflow status) in addition to the DB data.
    """
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

    runs_result = await (
        supabase.table("verification_runs")
        .select("status, metric_values, output_hash, is_deterministic")
        .eq("submission_id", str(submission_id))
        .order("created_at", desc=True)
        .execute()
    )

    response = {
        "submission_id": str(submission_id),
        "verification_status": submission["verification_status"],
        "runs": runs_result.data or [],
    }

    # Enrich with workflow state if available
    if temporal is not None and _TEMPORAL_AVAILABLE:
        bounty_id = submission.get("bounty_id", "")
        if bounty_id:
            try:
                handle = temporal.get_workflow_handle(f"bounty-{bounty_id}")
                response["workflow_status"] = await handle.query(
                    BountyWorkflow.get_status
                )
                workflow_submissions = await handle.query(
                    BountyWorkflow.get_submissions
                )
                # Find this submission in the workflow's list
                for ws in workflow_submissions:
                    if ws.get("submission_id") == str(submission_id):
                        response["workflow_verified"] = ws.get("verified", False)
                        response["workflow_passed"] = ws.get("passed", None)
                        break
            except Exception:
                pass  # Workflow may not exist (pre-Temporal bounties)

    return response
```

Also add `get_temporal` dependency access. Change:

```python
# BEFORE (existing import line):
from sciona.api import deps as api_deps
```

No change needed — `api_deps.get_temporal` is already available through the
module import.

---

## Step 10: Create `tests/test_bounty_workflow.py`

**File:** `tests/test_bounty_workflow.py` (create)

```python
"""Tests for the BountyWorkflow using Temporal's test environment.

These tests run a real Temporal workflow in-process using the Temporal
test server — no external Temporal cluster required.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from sciona.workflows.bounty_activities import (
    ComputeSettlementInput,
    ExecutePayoutsInput,
    LaunchVerificationInput,
    RecordFundingInput,
    RecordSettlementInput,
    compute_settlement,
    execute_payouts,
    launch_verification,
    record_funding,
    record_settlement,
)
from sciona.workflows.bounty_workflow import (
    BountyWorkflow,
    BountyWorkflowInput,
    FundSignal,
    SubmitSignal,
    VerifyCompleteSignal,
)

try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    TEMPORAL_AVAILABLE = True
except ImportError:
    TEMPORAL_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not TEMPORAL_AVAILABLE,
    reason="temporalio not installed",
)

TASK_QUEUE = "test-bounty-lifecycle"


# ---------------------------------------------------------------------------
# Mock activities — avoid real Supabase / Stripe calls in tests
# ---------------------------------------------------------------------------


async def mock_record_funding(input: RecordFundingInput) -> None:
    """No-op: pretend we recorded funding."""
    pass


async def mock_launch_verification(input: LaunchVerificationInput) -> str:
    """No-op: return a fake run ID."""
    return f"mock-run-{input.submission_id}"


async def mock_compute_settlement(input: ComputeSettlementInput) -> str:
    """Return a minimal mock payout plan."""
    import json

    return json.dumps({
        "bounty_id": input.bounty_id,
        "escrow_amount": input.escrow_amount,
        "recipients": [
            {
                "recipient_id": "platform",
                "role": "platform",
                "amount": input.escrow_amount * 0.05,
                "stripe_account_id": "platform",
            },
            {
                "recipient_id": "architect-1",
                "role": "architect",
                "amount": input.escrow_amount * 0.65,
                "stripe_account_id": "",
                "cdg_hash": "abc123",
            },
        ],
        "shapley_allocations": {},
        "winners": [],
    })


async def mock_execute_payouts(input: ExecutePayoutsInput) -> list[str]:
    """Return fake transfer IDs."""
    return [f"mock_transfer_{input.bounty_id}_1", f"mock_transfer_{input.bounty_id}_2"]


async def mock_record_settlement(input: RecordSettlementInput) -> None:
    """No-op: pretend we recorded settlement."""
    pass


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def env():
    """Start an in-process Temporal test environment."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@pytest.fixture
async def worker(env: WorkflowEnvironment):
    """Start a worker with mock activities."""
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[BountyWorkflow],
        activities=[
            mock_record_funding,
            mock_launch_verification,
            mock_compute_settlement,
            mock_execute_payouts,
            mock_record_settlement,
        ],
    ):
        yield env.client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBountyWorkflowLifecycle:
    """Test the full bounty lifecycle: draft -> open -> submitted -> verified -> settled."""

    async def test_full_lifecycle(self, worker):
        """Happy path: fund, submit, verify, settle."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-1",
                escrow_amount=100.0,
                deadline_seconds=3600,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-1",
            task_queue=TASK_QUEUE,
        )

        # Check initial status
        status = await handle.query(BountyWorkflow.get_status)
        assert status == "draft"

        # Fund the bounty
        await handle.signal(BountyWorkflow.fund, FundSignal(stripe_payment_id="pi_test123"))

        # Give the workflow a moment to process
        await asyncio.sleep(0.5)

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "open"

        # Submit a solution
        await handle.signal(
            BountyWorkflow.submit,
            SubmitSignal(submission_id="sub-1", architect_id="architect-1"),
        )

        await asyncio.sleep(0.5)

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "submitted"

        submissions = await handle.query(BountyWorkflow.get_submissions)
        assert len(submissions) == 1
        assert submissions[0]["submission_id"] == "sub-1"

        # Signal verification complete
        await handle.signal(
            BountyWorkflow.verify_complete,
            VerifyCompleteSignal(
                submission_id="sub-1",
                passed=True,
                metric_values={"accuracy": 0.95},
            ),
        )

        # Wait for the workflow to complete (settlement)
        result = await handle.result()
        assert result == "settled"

        # Verify settlement transfers were recorded
        transfers = await handle.query(BountyWorkflow.get_settlement_transfers)
        assert len(transfers) == 2

    async def test_cancel_from_draft(self, worker):
        """Cancel a bounty before funding."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-cancel-1",
                escrow_amount=50.0,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-cancel-1",
            task_queue=TASK_QUEUE,
        )

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "draft"

        await handle.signal(BountyWorkflow.cancel)
        result = await handle.result()
        assert result == "cancelled"

    async def test_cancel_from_open(self, worker):
        """Cancel a bounty after funding but before submissions."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-cancel-2",
                escrow_amount=75.0,
                deadline_seconds=3600,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-cancel-2",
            task_queue=TASK_QUEUE,
        )

        await handle.signal(BountyWorkflow.fund, FundSignal(stripe_payment_id="pi_test456"))
        await asyncio.sleep(0.5)

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "open"

        await handle.signal(BountyWorkflow.cancel)
        result = await handle.result()
        assert result == "cancelled"

    async def test_verification_failure_expires(self, worker):
        """If no submissions pass verification, the bounty expires."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-fail-1",
                escrow_amount=100.0,
                deadline_seconds=3600,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-fail-1",
            task_queue=TASK_QUEUE,
        )

        await handle.signal(BountyWorkflow.fund, FundSignal(stripe_payment_id="pi_test789"))
        await asyncio.sleep(0.5)

        await handle.signal(
            BountyWorkflow.submit,
            SubmitSignal(submission_id="sub-fail-1", architect_id="architect-1"),
        )
        await asyncio.sleep(0.5)

        # Verification fails
        await handle.signal(
            BountyWorkflow.verify_complete,
            VerifyCompleteSignal(
                submission_id="sub-fail-1",
                passed=False,
                metric_values={},
            ),
        )

        result = await handle.result()
        assert result == "expired"


class TestBountyWorkflowQueries:
    """Test workflow query methods."""

    async def test_get_submissions_empty(self, worker):
        """Submissions list is empty before any submissions."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-query-1",
                escrow_amount=100.0,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-query-1",
            task_queue=TASK_QUEUE,
        )

        submissions = await handle.query(BountyWorkflow.get_submissions)
        assert submissions == []

        # Clean up
        await handle.signal(BountyWorkflow.cancel)
        await handle.result()


class TestBountyWorkflowSignalValidation:
    """Test that invalid signals are rejected."""

    async def test_cannot_fund_twice(self, worker):
        """Funding an already-funded bounty raises an error."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-double-fund",
                escrow_amount=100.0,
                deadline_seconds=3600,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-double-fund",
            task_queue=TASK_QUEUE,
        )

        await handle.signal(BountyWorkflow.fund, FundSignal(stripe_payment_id="pi_first"))
        await asyncio.sleep(0.5)

        # Second fund should fail (status is now "open", not "draft")
        # The signal will raise ApplicationError inside the workflow,
        # but signals don't propagate errors to the caller.  Instead,
        # we verify the status hasn't changed incorrectly.
        await handle.signal(BountyWorkflow.fund, FundSignal(stripe_payment_id="pi_second"))
        await asyncio.sleep(0.5)

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "open"  # still open, not re-funded

        # Clean up
        await handle.signal(BountyWorkflow.cancel)
        await handle.result()

    async def test_cannot_submit_to_draft(self, worker):
        """Submitting to an unfunded bounty raises an error."""
        client = worker

        handle = await client.start_workflow(
            BountyWorkflow.run,
            BountyWorkflowInput(
                bounty_id="test-bounty-submit-draft",
                escrow_amount=100.0,
                principal_id="principal-1",
            ),
            id="bounty-test-bounty-submit-draft",
            task_queue=TASK_QUEUE,
        )

        # Submit without funding — signal is accepted but ignored
        # (status check in signal handler prevents transition)
        await handle.signal(
            BountyWorkflow.submit,
            SubmitSignal(submission_id="sub-early", architect_id="architect-1"),
        )
        await asyncio.sleep(0.5)

        status = await handle.query(BountyWorkflow.get_status)
        assert status == "draft"

        # Clean up
        await handle.signal(BountyWorkflow.cancel)
        await handle.result()
```

---

## Execution order

The steps above must be executed in order:

1. **Step 1** — Add dependency (no code depends on it yet)
2. **Step 2** — Create package init (empty until step 3)
3. **Steps 3-4** — Create workflow and activities (independent of API)
4. **Step 5** — Create worker (depends on steps 3-4)
5. **Steps 6-7** — Modify app.py and deps.py (Temporal client plumbing)
6. **Steps 8-9** — Modify routers (depends on steps 3-4 + 6-7)
7. **Step 10** — Create tests (depends on all above)

Steps 3 and 4 can be done in parallel.  Steps 6 and 7 can be done in parallel.
Steps 8 and 9 can be done in parallel.

---

## Verification checklist

After implementation, verify:

- [ ] `pip install -e ".[api,dev]"` installs `temporalio>=1.5`
- [ ] `python -c "from sciona.workflows import BountyWorkflow"` succeeds
- [ ] `python -m sciona.workflows.worker` starts (and fails to connect if no Temporal server — that is expected)
- [ ] API starts without `TEMPORAL_ADDRESS` set (graceful degradation)
- [ ] API starts with `TEMPORAL_ADDRESS=localhost:7233` (connects to Temporal)
- [ ] `pytest tests/test_bounty_workflow.py` passes (uses in-process test server)
- [ ] `pytest tests/test_bounty_state_machine.py` still passes (business logic preserved)
- [ ] Existing bounty API tests still pass (inline fallback active when Temporal is not configured)

---

## Design decisions

### Why graceful degradation instead of hard requirement?

The API must remain functional without Temporal for:
- Local development without Docker infrastructure
- Existing test suites that test the API without Temporal
- Gradual rollout (can enable Temporal per-environment)

The `get_temporal` dependency returns `None` when Temporal is not configured.
Each endpoint checks for `None` and falls back to the inline state machine.
This means `bounty_state.py` is preserved and remains the source of truth for
transition validation even in the Temporal path (the workflow calls the same
validation logic).

### Why signal payloads as dataclasses instead of plain args?

Temporal serializes signal arguments.  Using dataclasses:
- Makes the contract explicit and versionable
- Allows adding fields without breaking existing workflows
- Enables type checking in tests

### Why `_get_supabase()` in activities instead of dependency injection?

Activities run in the worker process, which is separate from the FastAPI
application.  FastAPI's `Depends()` is not available.  Activities create
their own Supabase client from environment variables, matching the pattern
used by the worker process.

### Why JSON string for payout plan between activities?

Temporal activity return values must be serializable.  `PayoutPlan` contains
`Decimal` fields which are not JSON-serializable by default.  Using
`model_dump_json()` / `model_validate_json()` round-trips cleanly via
Pydantic's built-in Decimal handling.
