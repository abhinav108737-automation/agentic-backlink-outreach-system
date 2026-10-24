"""Job handler factories for `replies.ingest` and `replies.classify`."""

from __future__ import annotations

from kb_contracts.domain import ScheduledJob
from kb_contracts.providers import EmailProvider

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.outreach.payloads import RepliesClassifyJob, RepliesIngestJob, parse_payload
from kb_core.outreach.ports import JobHandler, TransitionPort
from kb_core.replies.service import ReplyService


def make_ingest_handler(
    uow_factory: UnitOfWorkFactory, email: EmailProvider, transitions: TransitionPort
) -> JobHandler:
    """`replies.ingest`: poll the provider, store new replies once, enqueue classification."""
    service = ReplyService(uow_factory, transitions)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(RepliesIngestJob, job)
        await service.poll_and_ingest(
            email,
            campaign_id=p.campaign_id,
            sequence_id=p.sequence_id,
            correlation_id=p.correlation_id,
        )

    return handle


def make_classify_handler(
    uow_factory: UnitOfWorkFactory, transitions: TransitionPort
) -> JobHandler:
    """`replies.classify`: deterministic classification and its consequences, once."""
    service = ReplyService(uow_factory, transitions)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(RepliesClassifyJob, job)
        await service.classify(p.reply_id, correlation_id=p.correlation_id)

    return handle
