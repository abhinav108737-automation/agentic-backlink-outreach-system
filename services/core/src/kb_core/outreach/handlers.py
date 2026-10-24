"""Job handler factories for `outreach.draft`, `outreach.send` and `outreach.follow_up`.

Each factory returns a plain `Callable[[ScheduledJob], Awaitable[None]]`; the Lead assembles the
registry. Every handler is idempotent under redelivery (see the service it wraps).
"""

from __future__ import annotations

from kb_contracts.domain import ScheduledJob
from kb_contracts.policy import PolicyEngine
from kb_contracts.providers import Clock, EmailProvider

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.outreach.context import DraftProducer, load_draft_context
from kb_core.outreach.drafting import DraftingService, DraftProvenance
from kb_core.outreach.followup import FollowUpService
from kb_core.outreach.payloads import (
    DraftJob,
    FollowUpJob,
    SendJob,
    parse_payload,
)
from kb_core.outreach.ports import JobHandler, TransitionPort
from kb_core.outreach.send import DeliveryService, SendGate


def make_draft_handler(
    uow_factory: UnitOfWorkFactory,
    transitions: TransitionPort,
    producer: DraftProducer,
    *,
    provenance: DraftProvenance | None = None,
    route_for_send: bool = True,
    similarity_threshold: float = 0.8,
) -> JobHandler:
    """`outreach.draft`: produce a draft, QA it, and (when it passes) route it to approval."""
    service = DraftingService(uow_factory, transitions, similarity_threshold=similarity_threshold)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(DraftJob, job)
        async with uow_factory() as uow:  # short read transaction; no lock held while drafting
            ctx = await load_draft_context(uow, p.sequence_id, p.step_no)
        draft = await producer(ctx)
        outcome = await service.submit(
            draft, correlation_id=p.correlation_id, provenance=provenance
        )
        if route_for_send and outcome.message.status.value == "QA_PASSED":
            await service.route_for_send(outcome.message.id, correlation_id=p.correlation_id)

    return handle


def make_send_handler(
    uow_factory: UnitOfWorkFactory,
    email: EmailProvider,
    gate: SendGate,
    transitions: TransitionPort,
    clock: Clock,
) -> JobHandler:
    """`outreach.send`: gate (Lane B `begin_send`), deliver, complete. Raises only to request a
    bounded retry (transient block or retryable provider error)."""
    service = DeliveryService(uow_factory, email, gate, transitions, clock)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(SendJob, job)
        await service.send(p.message_id, correlation_id=p.correlation_id)

    return handle


def make_follow_up_handler(
    uow_factory: UnitOfWorkFactory,
    transitions: TransitionPort,
    policy: PolicyEngine,
    clock: Clock,
) -> JobHandler:
    """`outreach.follow_up`: fire the timer; draft the next step or complete the sequence."""
    service = FollowUpService(uow_factory, transitions, policy)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(FollowUpJob, job)
        await service.run(
            p.sequence_id, p.step_no, correlation_id=p.correlation_id, run_at_now=clock.now()
        )

    return handle
