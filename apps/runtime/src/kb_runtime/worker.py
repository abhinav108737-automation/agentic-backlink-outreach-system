"""Bounded worker entrypoint: the separate process that executes approved typed jobs.

FastAPI only ever enqueues (`apps/api` must not import `kb_agents`, and does not import
`kb_runtime` either). This module is the other side: it binds the typed job registry to real
services, providers and agents, and drains the PostgreSQL queue a bounded number of times.

There is no always-on loop. `--iterations N` drains at most N passes and stops on the first
empty pass, so a run always terminates. A supervisor re-invoking this is a deployment choice,
not something the code assumes.

    uv run python -m kb_runtime.worker --iterations 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from kb_contracts.config import Settings
from kb_core.db.engine import create_db_engine
from kb_core.db.uow import SqlUnitOfWorkFactory
from kb_core.scheduling.clock import SystemClock
from kb_core.scheduling.worker import DrainResult, JobWorker

from kb_runtime.composition import build_outreach_stack

log = logging.getLogger("kb_runtime.worker")


async def drain(*, iterations: int, lease_seconds: int, batch_size: int) -> DrainResult:
    settings = Settings()
    engine = create_db_engine()
    try:
        factory = SqlUnitOfWorkFactory(engine)
        stack = build_outreach_stack(settings=settings, uow_factory=factory, clock=SystemClock())
        worker = JobWorker(
            factory,
            SystemClock(),
            stack.registry,
            lease_owner=f"kb-runtime-{iterations}",
            lease_seconds=lease_seconds,
            batch_size=batch_size,
        )
        return await worker.run_iterations(iterations)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb_runtime.worker", description=__doc__)
    p.add_argument("--iterations", type=int, default=1, help="bounded number of drain passes")
    p.add_argument("--lease-seconds", type=int, default=300)
    p.add_argument("--batch-size", type=int, default=10)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(
        drain(
            iterations=args.iterations,
            lease_seconds=args.lease_seconds,
            batch_size=args.batch_size,
        )
    )
    log.info(
        "drained claimed=%d succeeded=%d failed=%d unhandled=%d lost_lease=%d",
        result.claimed,
        result.succeeded,
        result.failed,
        result.unhandled,
        result.lost_lease,
    )
    # A dead-lettered or failed job is surfaced, not swallowed: a supervisor can alert on it.
    return 0 if result.failed == 0 and result.unhandled == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
