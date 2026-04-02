"""Standalone Temporal worker for bounty workflows."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sciona_infra.workflows import (
    BountyWorkflow,
    compute_settlement,
    execute_payouts,
    launch_verification,
    record_funding,
    record_settlement,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    try:
        from temporalio.client import Client as TemporalClient
        from temporalio.worker import Worker
    except ImportError:
        logger.warning("temporalio is not installed; Temporal worker is disabled")
        return

    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    client = await TemporalClient.connect(address)
    worker = Worker(
        client,
        task_queue="bounty-lifecycle",
        workflows=[BountyWorkflow],
        activities=[
            record_funding,
            launch_verification,
            compute_settlement,
            execute_payouts,
            record_settlement,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
