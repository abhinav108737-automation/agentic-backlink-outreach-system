"""Job handler factory for `backlink.verify`."""

from __future__ import annotations

from datetime import timedelta

from kb_contracts.domain import ScheduledJob
from kb_contracts.enums import BacklinkStatus
from kb_contracts.providers import Clock, CrawlerProvider

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.outreach.payloads import BACKLINK_VERIFY, BacklinkVerifyJob, parse_payload
from kb_core.outreach.ports import JobHandler, TransitionPort
from kb_core.verification.service import DEFAULT_LOST_AFTER_MISSES, VerificationService


def make_verify_handler(
    uow_factory: UnitOfWorkFactory,
    crawler: CrawlerProvider,
    transitions: TransitionPort,
    clock: Clock,
    *,
    lost_after_misses: int = DEFAULT_LOST_AFTER_MISSES,
    recheck_every: timedelta | None = None,
) -> JobHandler:
    """`backlink.verify`: one verification pass. A redelivered job only appends another check
    row (the table is append-only); it can never create a second backlink or skip a guard. When
    `recheck_every` is set, the next check is scheduled until the backlink is LOST."""
    service = VerificationService(
        uow_factory, crawler, transitions, clock, lost_after_misses=lost_after_misses
    )

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(BacklinkVerifyJob, job)
        result = await service.verify(p.backlink_id, correlation_id=p.correlation_id)
        if recheck_every is None or result.backlink.status is BacklinkStatus.LOST:
            return
        when = clock.now() + recheck_every
        corr = f"{p.correlation_id}:recheck:{when.isoformat()}"
        async with uow_factory() as uow:
            await uow.jobs.enqueue(
                job_type=BACKLINK_VERIFY,
                payload={"backlink_id": str(p.backlink_id), "correlation_id": corr},
                run_at=when,
                idempotency_key=f"{BACKLINK_VERIFY}:{p.backlink_id}:{corr}",
                concurrency_key=f"backlink:{p.backlink_id}",
                subject_type="backlink",
                subject_id=p.backlink_id,
            )

    return handle
