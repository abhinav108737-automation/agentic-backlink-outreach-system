"""Reply ingestion and classification effects.

`ingest` stores each inbound email once (the repository dedupes on `provider_message_id`, and this
service re-checks the `created` flag so a re-poll enqueues no second classification).
`classify` persists a deterministic classification and applies its consequences in ONE unit of
work, which makes redelivery harmless: a reply that already has a classification is returned
unchanged and nothing is applied twice.

Consequences (state moves go through `TransitionPort`; Lane B's service also cancels the
sequence's jobs and unsent messages, and suppresses the channel on OPTED_OUT / BOUNCED):

  bounce        sequence -> BOUNCED; the sent message -> BOUNCED
  opt_out       sequence -> OPTED_OUT
  out_of_office sequence stays as it is; next_due_at moves to the day after the return date
  positive / negotiation
                sequence -> REPLIED; a guest-post opportunity is opened (once per sequence);
                `negotiation.plan` is enqueued
  question / negative / unknown
                sequence -> REPLIED (a human or the Reply agent follows up)

An opt-out or bounce that arrives after the sequence is already terminal (for example REPLIED)
cannot move the sequence, but the address is still suppressed directly: a stop request is never
dropped.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from kb_contracts.domain import OutreachSequence, Reply, ReplyClassification
from kb_contracts.enums import (
    MessageStatus,
    ReplyClass,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.events import Actor
from kb_contracts.providers import EmailProvider, InboundEmail

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.outreach.payloads import NEGOTIATION_PLAN, REPLIES_CLASSIFY, sequence_concurrency_key
from kb_core.outreach.ports import ACTOR_REPLIES, TransitionPort
from kb_core.outreach.send import load_channel
from kb_core.replies.classify import ClassificationOutcome, classify_reply
from kb_core.replies.sanitize import sanitize_email_body
from kb_core.suppression.service import SuppressionService, check_suppression

log = logging.getLogger("kb_core.replies")

_OPEN_SEQUENCE = (
    SequenceStatus.PLANNED,
    SequenceStatus.READY,
    SequenceStatus.POLICY_BLOCKED,
    SequenceStatus.WAITING_REPLY,
    SequenceStatus.FOLLOW_UP_DUE,
)
_OPPORTUNITY_CLASSES = (ReplyClass.POSITIVE, ReplyClass.NEGOTIATION)


@dataclass
class IngestSummary:
    created: list[UUID] = field(default_factory=list)
    duplicates: int = 0
    unmatched: int = 0


@dataclass(frozen=True)
class ClassifiedReply:
    classification: ReplyClassification
    outcome: ClassificationOutcome | None  # None when it had already been classified
    already_classified: bool


def stored_body(email: InboundEmail) -> str:
    """`replies.body_sanitized`: the subject line first (rules read it), then the clean body."""
    subject = " ".join(email.subject.split())[:300]
    return f"Subject: {subject}\n\n{sanitize_email_body(email.body)}"


def raw_hash(email: InboundEmail) -> str:
    return hashlib.sha256(f"{email.subject}\n{email.body}".encode()).hexdigest()


class ReplyService:
    def __init__(self, uow_factory: UnitOfWorkFactory, transitions: TransitionPort) -> None:
        self._uow_factory = uow_factory
        self._tr = transitions

    # -- ingestion ------------------------------------------------------------------------------
    async def ingest(
        self,
        emails: list[InboundEmail],
        *,
        campaign_id: UUID,
        sequence_id: UUID | None,
        correlation_id: str,
        actor: Actor = ACTOR_REPLIES,
    ) -> IngestSummary:
        summary = IngestSummary()
        for email in emails:
            async with self._uow_factory() as uow:
                seq = await self._match(uow, email, campaign_id, sequence_id)
                if seq is None:
                    summary.unmatched += 1
                    continue
                thread = await uow.threads.get_or_create_for_sequence(seq.id, subject=email.subject)
                reply, created = await uow.replies.ingest(
                    sequence_id=seq.id,
                    thread_id=thread["id"],
                    provider_message_id=email.provider_message_id,
                    from_normalized=email.from_address,
                    received_at=email.received_at,
                    body_sanitized=stored_body(email),
                    raw_hash=raw_hash(email),
                )
                if not created:
                    summary.duplicates += 1
                    continue
                await AuditTrail(uow.audit).record(
                    actor=actor,
                    action="reply.ingested",
                    subject_type="reply",
                    subject_id=reply.id,
                    correlation_id=correlation_id,
                    new_state={"sequence_id": str(seq.id), "raw_hash": reply.raw_hash},
                )
                await uow.jobs.enqueue(
                    job_type=REPLIES_CLASSIFY,
                    payload={
                        "reply_id": str(reply.id),
                        "sequence_id": str(seq.id),
                        "correlation_id": correlation_id,
                    },
                    run_at=reply.received_at,
                    idempotency_key=f"{REPLIES_CLASSIFY}:{reply.id}",
                    concurrency_key=sequence_concurrency_key(seq.id),
                    subject_type="sequence",
                    subject_id=seq.id,
                )
                summary.created.append(reply.id)
        return summary

    async def poll_and_ingest(
        self,
        email: EmailProvider,
        *,
        campaign_id: UUID,
        sequence_id: UUID | None,
        correlation_id: str,
    ) -> IngestSummary:
        return await self.ingest(
            await email.fetch_replies(),
            campaign_id=campaign_id,
            sequence_id=sequence_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    async def _match(
        uow: UnitOfWork, email: InboundEmail, campaign_id: UUID, only: UUID | None
    ) -> OutreachSequence | None:
        """Thread reference first (it also matches a bounce from mailer-daemon), then sender."""
        found: OutreachSequence | None = None
        if email.thread_ref:
            try:
                thread = await uow.threads.get(UUID(email.thread_ref))
            except ValueError:
                thread = None
            if thread is not None:
                found = await uow.sequences.get(thread["sequence_id"])
        if found is None:
            sender = email.from_address.strip().lower()
            cursor: str | None = None
            while found is None:
                page = await uow.sequences.list(campaign_id=campaign_id, cursor=cursor, limit=200)
                for seq in page.items:
                    try:
                        channel = await load_channel(uow, seq)
                    except EntityNotFound:
                        continue
                    if channel.value_normalized == sender:
                        found = seq
                cursor = page.next_cursor
                if cursor is None:
                    break
        if found is None or found.campaign_id != campaign_id:
            return None
        return found if only is None or found.id == only else None

    # -- classification ---------------------------------------------------------------------------
    async def classify(
        self, reply_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_REPLIES
    ) -> ClassifiedReply:
        async with self._uow_factory() as uow:
            reply = await uow.replies.get(reply_id)
            if reply is None:
                raise EntityNotFound("reply", reply_id)
            prior = await uow.replies.latest_classification(reply_id)
            if prior is not None:
                return ClassifiedReply(prior, None, already_classified=True)
            outcome = classify_reply(
                reply.from_normalized, reply.body_sanitized, received_at=reply.received_at
            )
            res = outcome.result
            saved = await uow.replies.record_classification(
                reply_id=reply.id,
                reply_class=res.reply_class.value,
                confidence=res.confidence,
                next_action=res.next_action,
                deterministic_rule_applied=outcome.rule,
                return_date=outcome.return_date,
            )
            await AuditTrail(uow.audit).record(
                actor=actor,
                action="reply.classified",
                subject_type="reply",
                subject_id=reply.id,
                correlation_id=correlation_id,
                new_state={
                    "class": res.reply_class.value,
                    "rule": outcome.rule,
                    "classifier_version": outcome.classifier_version,
                },
            )
            await self._apply(uow, reply, outcome, actor, correlation_id)
            return ClassifiedReply(saved, outcome, already_classified=False)

    async def _apply(
        self,
        uow: UnitOfWork,
        reply: Reply,
        outcome: ClassificationOutcome,
        actor: Actor,
        correlation_id: str,
    ) -> None:
        seq = await uow.sequences.get(reply.sequence_id)
        if seq is None:
            raise EntityNotFound("outreach_sequence", reply.sequence_id)
        cls = outcome.result.reply_class
        why = f"reply {reply.id} classified {cls.value} by {outcome.rule}"

        if cls is ReplyClass.OUT_OF_OFFICE:
            if outcome.return_date is not None:
                await uow.sequences.set_next_due(
                    seq.id, run_at=outcome.return_date + timedelta(days=1)
                )
            return

        if cls is ReplyClass.BOUNCE:
            await self._bounce_message(uow, seq, actor, why, correlation_id)
        if cls in (ReplyClass.BOUNCE, ReplyClass.OPT_OUT):
            target = (
                SequenceStatus.BOUNCED if cls is ReplyClass.BOUNCE else SequenceStatus.OPTED_OUT
            )
            if seq.status in _OPEN_SEQUENCE:
                await self._tr.transition_in(
                    uow, seq, target, actor=actor, reason=why, correlation_id=correlation_id
                )
            else:  # already terminal: the sequence cannot move, but the stop must still hold
                await self._suppress_directly(uow, seq, cls, actor, correlation_id)
            return

        if seq.status in _OPEN_SEQUENCE:
            seq = (
                await self._tr.transition_in(
                    uow,
                    seq,
                    SequenceStatus.REPLIED,
                    actor=actor,
                    reason=why,
                    correlation_id=correlation_id,
                )
            ).entity
        if cls in _OPPORTUNITY_CLASSES:
            await self._open_opportunity(uow, seq, reply, correlation_id)

    async def _bounce_message(
        self, uow: UnitOfWork, seq: OutreachSequence, actor: Actor, why: str, correlation_id: str
    ) -> None:
        sent = [
            m
            for m in await uow.messages.list_for_sequence(seq.id)
            if m.status is MessageStatus.SENT
        ]
        if sent:
            latest = max(sent, key=lambda m: m.step_no)
            await self._tr.transition_in(
                uow,
                latest,
                MessageStatus.BOUNCED,
                actor=actor,
                reason=why,
                correlation_id=correlation_id,
            )

    @staticmethod
    async def _suppress_directly(
        uow: UnitOfWork,
        seq: OutreachSequence,
        cls: ReplyClass,
        actor: Actor,
        correlation_id: str,
    ) -> None:
        channel = await load_channel(uow, seq)
        if channel.channel_type != "email":
            return
        found = await check_suppression(uow, campaign_id=None, email=channel.value_normalized)
        if any(
            e.scope is SuppressionScope.EMAIL and e.value_normalized == channel.value_normalized
            for e in found.entries
        ):
            return
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=channel.value_normalized,
            reason=SuppressionReason.BOUNCE
            if cls is ReplyClass.BOUNCE
            else SuppressionReason.OPT_OUT,
            actor=actor,
            correlation_id=correlation_id,
            source_event_ref=f"outreach_sequence:{seq.id}:late_{cls.value}",
        )

    @staticmethod
    async def _open_opportunity(
        uow: UnitOfWork, seq: OutreachSequence, reply: Reply, correlation_id: str
    ) -> None:
        cursor: str | None = None
        while True:
            page = await uow.opportunities.list(cursor=cursor, limit=200)
            if any(o.sequence_id == seq.id for o in page.items):
                opportunity_id = next(o.id for o in page.items if o.sequence_id == seq.id)
                break
            cursor = page.next_cursor
            if cursor is None:
                targets = await uow.campaigns.list_targets(seq.campaign_id)
                created = await uow.opportunities.create(
                    prospect_id=seq.prospect_id,
                    sequence_id=seq.id,
                    target_url=targets[0].target_url if targets else None,
                )
                opportunity_id = created.id
                await AuditTrail(uow.audit).record(
                    actor=ACTOR_REPLIES,
                    action="opportunity.opened",
                    subject_type="opportunity",
                    subject_id=created.id,
                    correlation_id=correlation_id,
                    new_state={"reply_id": str(reply.id), "sequence_id": str(seq.id)},
                )
                break
        payload: dict[str, Any] = {
            "reply_id": str(reply.id),
            "sequence_id": str(seq.id),
            "correlation_id": correlation_id,
        }
        await uow.jobs.enqueue(
            job_type=NEGOTIATION_PLAN,
            payload=payload,
            run_at=reply.received_at,
            idempotency_key=f"{NEGOTIATION_PLAN}:{reply.id}",
            concurrency_key=sequence_concurrency_key(seq.id),
            subject_type="opportunity",
            subject_id=opportunity_id,
        )
