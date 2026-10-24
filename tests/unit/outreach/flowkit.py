"""Shared helpers for the Lane E flow tests (plain module; imported by sibling test files)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from kb_contracts.domain import (
    Campaign,
    EvidenceItem,
    OutreachMessage,
    OutreachSequence,
    Prospect,
    ScheduledJob,
)
from kb_contracts.enums import ApprovalStatus, JobStatus, ProspectStatus
from kb_core.audit.service import OPERATOR
from kb_core.db.uow import SqlUnitOfWork, SqlUnitOfWorkFactory
from kb_core.outreach.context import TemplateDraftProducer
from kb_core.outreach.handlers import make_draft_handler, make_send_handler
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.scheduling.clock import FakeClock
from kb_core.sending.service import SendService as GateService
from kb_core.transitions.service import TransitionService
from kb_providers.email import MockEmailProvider

T0 = datetime(2026, 3, 2, 9, tzinfo=UTC)


@dataclass
class World:
    factory: SqlUnitOfWorkFactory
    clock: FakeClock
    transitions: TransitionService
    policy: DeterministicPolicyEngine
    gate: GateService
    campaign: Campaign
    prospect: Prospect
    sequence: OutreachSequence
    evidence: list[EvidenceItem]
    channel_email: str
    domain_name: str

    async def sql(self, statement: str, **params: Any) -> list[Any]:
        async with self.factory() as uow:
            assert isinstance(uow, SqlUnitOfWork)
            result = await uow.session.execute(sa.text(statement), params)
            return list(result.mappings().all()) if result.returns_rows else []

    async def new_prospect_sequence(
        self, domain: str, email: str
    ) -> tuple[Prospect, OutreachSequence, list[EvidenceItem]]:
        return await seed_prospect(self.factory, self.campaign, domain, email)


async def seed_prospect(
    factory: SqlUnitOfWorkFactory, campaign: Campaign, domain: str, email: str
) -> tuple[Prospect, OutreachSequence, list[EvidenceItem]]:
    async with factory() as uow:
        d = await uow.domains.get_or_create(domain)
        p = await uow.prospects.create(campaign_id=campaign.id, domain_id=d.id)
        for status in (
            ProspectStatus.ELIGIBLE,
            ProspectStatus.RANKED,
            ProspectStatus.RESEARCHED,
            ProspectStatus.CONTACT_READY,
            ProspectStatus.OUTREACH_READY,
        ):
            p = await uow.prospects.set_status(p.id, status, expected_version=p.version)
        contact = await uow.contacts.create(
            domain_id=d.id, name="Editor", role="editor", contact_type="person", confidence=0.9
        )
        channel = await uow.contacts.add_channel(
            contact_id=contact.id, channel_type="email", value_normalized=email, confidence=0.9
        )
        seq = await uow.sequences.create(
            campaign_id=campaign.id, prospect_id=p.id, contact_channel_id=channel.id
        )
        items = await uow.evidence.record_many(
            p.id,
            [
                {
                    "source_url": f"https://{domain}/about",
                    "excerpt": excerpt,
                    "content_hash": f"h{i}-{domain}",
                    "kind": "topic",
                    "extracted_at": T0,
                    "extractor": "deterministic",
                }
                for i, excerpt in enumerate(
                    [
                        "Weekly coverage of healthcare market research and industry analysis",
                        "Our editors publish original data-driven reports on medical devices",
                    ]
                )
            ],
        )
    return p, seq, list(items)


CORR = "corr-lane-e"


def job(job_type: str, payload: dict[str, Any]) -> ScheduledJob:
    now = datetime(2026, 3, 2, tzinfo=UTC)
    return ScheduledJob(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        job_type=job_type,
        payload={"correlation_id": CORR, **payload},
        run_at=now,
        status=JobStatus.RUNNING,
        idempotency_key=f"test:{uuid4()}",
    )


async def messages(world: World, sequence_id: UUID | None = None) -> list[OutreachMessage]:
    async with world.factory() as uow:
        return list(await uow.messages.list_for_sequence(sequence_id or world.sequence.id))


async def draft_step(world: World, step_no: int = 1, sequence_id: UUID | None = None) -> None:
    handler = make_draft_handler(world.factory, world.transitions, TemplateDraftProducer())
    seq_id = sequence_id or world.sequence.id
    await handler(job("outreach.draft", {"sequence_id": str(seq_id), "step_no": step_no}))


async def approved_and_queued(world: World, step_no: int = 1) -> OutreachMessage:
    """draft (QA) -> operator approval -> enqueue_send, through Lane B's real send service."""
    await draft_step(world, step_no)
    message = [m for m in await messages(world) if m.step_no == step_no][-1]
    await world.gate.approve(
        message.id,
        decision=ApprovalStatus.APPROVED,
        actor=OPERATOR,
        reason="ok",
        correlation_id=CORR,
    )
    await world.gate.enqueue_send(message.id, actor=OPERATOR, correlation_id=CORR)
    return [m for m in await messages(world) if m.step_no == step_no][-1]


def send_handler(world: World, provider: MockEmailProvider | None = None):
    provider = provider or MockEmailProvider(clock=world.clock)
    return provider, make_send_handler(
        world.factory, provider, world.gate, world.transitions, world.clock
    )


def send_job(message: OutreachMessage) -> ScheduledJob:
    return job(
        "outreach.send", {"message_id": str(message.id), "sequence_id": str(message.sequence_id)}
    )


async def sent_message(world: World, provider: MockEmailProvider | None = None):
    """Draft -> approve -> queue -> mock-send, returning (message, provider)."""
    m = await approved_and_queued(world)
    provider, handler = send_handler(world, provider)
    await handler(send_job(m))
    return m, provider
