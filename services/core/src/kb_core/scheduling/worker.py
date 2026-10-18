"""Bounded job worker: drain once, or run at most N iterations. There is no always-on loop.

A claim is its own committed unit of work (it sets the lease). The handler then runs outside any
transaction, and the outcome is written in a second unit of work. If the process dies between
the two, the lease expires and `reclaim_expired` returns the job to the queue with `attempts`
incremented, so retries stay bounded by `max_attempts` and exhausted jobs dead-letter.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from kb_contracts.domain import ScheduledJob
from kb_contracts.enums import JobStatus
from kb_contracts.providers import Clock

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.db.repos_runtime import InvalidJobTransition

log = logging.getLogger("kb_core.scheduling")

JobHandler = Callable[[ScheduledJob], Awaitable[None]]
MAX_ITERATIONS = 1000
_ERROR_CHARS = 300


@dataclass(frozen=True)
class DrainResult:
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0  # handler raised; the repository decides RETRY_WAIT vs DEAD by attempts
    unhandled: int = 0  # no handler registered for the job type -> dead-lettered
    lost_lease: int = 0

    def __add__(self, other: DrainResult) -> DrainResult:
        return DrainResult(
            self.claimed + other.claimed,
            self.succeeded + other.succeeded,
            self.failed + other.failed,
            self.unhandled + other.unhandled,
            self.lost_lease + other.lost_lease,
        )


class JobWorker:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        handlers: Mapping[str, JobHandler],
        *,
        lease_owner: str = "worker-1",
        lease_seconds: int = 300,
        batch_size: int = 10,
    ) -> None:
        if lease_seconds < 1 or batch_size < 1:
            raise ValueError("lease_seconds and batch_size must be >= 1")
        self._uow_factory = uow_factory
        self._clock = clock
        self._handlers = dict(handlers)
        self._owner = lease_owner
        self._lease_seconds = lease_seconds
        self._batch = batch_size

    async def drain_once(self) -> DrainResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await uow.jobs.reclaim_expired(now=now)
            jobs = await uow.jobs.claim_due(
                now=now,
                lease_owner=self._owner,
                lease_seconds=self._lease_seconds,
                limit=self._batch,
            )
        result = DrainResult(claimed=len(jobs))
        for job in jobs:
            result += await self._run_one(job)
        return result

    async def run_iterations(self, n: int) -> DrainResult:
        """At most `n` drain passes; stops early as soon as a pass claims nothing."""
        if not 1 <= n <= MAX_ITERATIONS:
            raise ValueError(f"n must be within 1..{MAX_ITERATIONS}")
        total = DrainResult()
        for _ in range(n):
            step = await self.drain_once()
            total += step
            if step.claimed == 0:
                break
        return total

    async def _run_one(self, job: ScheduledJob) -> DrainResult:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            outcome, error = JobStatus.DEAD, f"no handler registered for {job.job_type!r}"
        else:
            try:
                await handler(job)
                outcome, error = JobStatus.SUCCEEDED, None
            except Exception as exc:  # noqa: BLE001 - a handler failure must never kill the worker
                outcome = JobStatus.RETRY_WAIT
                error = f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
                log.warning("job %s (%s) failed: %s", job.id, job.job_type, type(exc).__name__)
        try:
            async with self._uow_factory() as uow:
                await uow.jobs.complete(job.id, status=outcome, last_error=error)
        except InvalidJobTransition:
            log.warning("job %s lost its lease before completion", job.id)
            return DrainResult(lost_lease=1)
        if handler is None:
            return DrainResult(unhandled=1)
        return DrainResult(succeeded=1) if error is None else DrainResult(failed=1)
