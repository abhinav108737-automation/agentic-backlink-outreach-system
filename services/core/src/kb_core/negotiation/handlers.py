"""Job handler factory for `negotiation.plan`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from kb_contracts.agents import NegotiationPlan
from kb_contracts.domain import GuestPostOpportunity, Reply, ReplyClassification, ScheduledJob

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.negotiation.service import NegotiationService
from kb_core.outreach.payloads import NegotiationPlanJob, parse_payload
from kb_core.outreach.ports import JobHandler, TransitionPort

PlanProducer = Callable[
    [GuestPostOpportunity, Reply, ReplyClassification | None], Awaitable[NegotiationPlan]
]
"""Produces a plan from the opportunity and the (untrusted) reply. In production the Lead wires
the Negotiation agent; offline, a deterministic producer. The reply body is data, not a prompt."""


def make_negotiation_handler(
    uow_factory: UnitOfWorkFactory,
    transitions: TransitionPort,
    producer: PlanProducer,
) -> JobHandler:
    """`negotiation.plan`: plan one round for the reply's opportunity (once per reply)."""
    service = NegotiationService(uow_factory, transitions)

    async def handle(job: ScheduledJob) -> None:
        p = parse_payload(NegotiationPlanJob, job)
        async with uow_factory() as uow:
            reply = await uow.replies.get(p.reply_id)
            if reply is None:
                raise EntityNotFound("reply", p.reply_id)
            classification = await uow.replies.latest_classification(reply.id)
            opp = await _opportunity_for(uow_factory, p.sequence_id)
        plan = await producer(opp, reply, classification)
        await service.plan_round(
            plan, correlation_id=p.correlation_id, idempotency_key=str(p.reply_id)
        )

    return handle


async def _opportunity_for(
    uow_factory: UnitOfWorkFactory, sequence_id: UUID
) -> GuestPostOpportunity:
    async with uow_factory() as uow:
        cursor: str | None = None
        while True:
            page = await uow.opportunities.list(cursor=cursor, limit=200)
            for opp in page.items:
                if opp.sequence_id == sequence_id:
                    return opp
            cursor = page.next_cursor
            if cursor is None:
                raise EntityNotFound("opportunity for sequence", sequence_id)
