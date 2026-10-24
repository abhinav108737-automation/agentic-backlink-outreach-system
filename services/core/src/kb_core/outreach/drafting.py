"""Persist an evidenced draft, run deterministic QA, and route it onward.

`DraftingService.submit` is one unit of work: the message row, its QA record and the
DRAFT -> QA_PASSED | QA_FAILED transition commit together or not at all. It is safe to call twice
for the same step (the worker may redeliver `outreach.draft`): a step that already has a live
message returns that message unchanged.

Proof that a claim is supported is *not* taken from the draft. `run_qa` re-reads the cited evidence
rows (`EvidenceRepository.get_many`) and checks they exist and belong to this prospect, and the
transition guard (`claims_supported`, Lane B) checks it again at the state change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from kb_contracts.agents import DraftMessage
from kb_contracts.domain import OutreachMessage, OutreachSequence
from kb_contracts.enums import ApprovalKind, MessageStatus, SequenceStatus
from kb_contracts.events import Actor

from kb_core.approvals.service import ApprovalService
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, InvalidInput, PolicyUnavailable
from kb_core.outreach.ports import (
    ACTOR_OUTREACH,
    TEMPLATE_VERSION,
    TransitionPort,
)
from kb_core.outreach.qa import QaResult, run_qa

# A step whose messages are all in these states needs a fresh draft (a new message_version).
_SUPERSEDED = frozenset(
    {
        MessageStatus.QA_FAILED,
        MessageStatus.REJECTED,
        MessageStatus.CANCELLED,
        MessageStatus.POLICY_BLOCKED,
        MessageStatus.FAILED,
    }
)
_SENT = frozenset({MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED})
MAX_DRAFT_VERSIONS = 3


@dataclass(frozen=True)
class DraftProvenance:
    prompt_version: str | None = None
    template_version: str | None = TEMPLATE_VERSION
    model_ref: str | None = None
    agent_run_id: UUID | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "template_version": self.template_version,
            "model_ref": self.model_ref,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
        }


@dataclass(frozen=True)
class DraftOutcome:
    message: OutreachMessage
    qa: QaResult | None  # None when an existing live message was returned unchanged
    created: bool


async def previously_sent_for_domain(uow: UnitOfWork, domain_id: UUID) -> list[OutreachMessage]:
    """Messages that left the system toward this registered domain, in any campaign."""
    sent: list[OutreachMessage] = []
    cursor: str | None = None
    while True:
        campaigns = await uow.campaigns.list(cursor=cursor, limit=100)
        for campaign in campaigns.items:
            prospect = await uow.prospects.get_by_domain(campaign.id, domain_id)
            if prospect is None:
                continue
            seq_cursor: str | None = None
            while True:
                seqs = await uow.sequences.list(
                    campaign_id=campaign.id, cursor=seq_cursor, limit=200
                )
                for seq in seqs.items:
                    if seq.prospect_id == prospect.id:
                        sent += [
                            m
                            for m in await uow.messages.list_for_sequence(seq.id)
                            if m.status in _SENT
                        ]
                seq_cursor = seqs.next_cursor
                if seq_cursor is None:
                    break
        cursor = campaigns.next_cursor
        if cursor is None:
            return sent


class DraftingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        transitions: TransitionPort,
        *,
        similarity_threshold: float = 0.8,
    ) -> None:
        self._uow_factory = uow_factory
        self._transitions = transitions
        self._threshold = similarity_threshold

    async def submit(
        self,
        draft: DraftMessage,
        *,
        correlation_id: str,
        provenance: DraftProvenance | None = None,
        actor: Actor = ACTOR_OUTREACH,
    ) -> DraftOutcome:
        prov = provenance or DraftProvenance()
        async with self._uow_factory() as uow:
            seq = await uow.sequences.get(draft.sequence_id)
            if seq is None:
                raise EntityNotFound("outreach_sequence", draft.sequence_id)
            existing = [
                m
                for m in await uow.messages.list_for_sequence(seq.id)
                if m.step_no == draft.step_no
            ]
            live = [m for m in existing if m.status not in _SUPERSEDED]
            if live:
                return DraftOutcome(live[-1], None, created=False)
            if len(existing) >= MAX_DRAFT_VERSIONS:
                raise InvalidInput(
                    f"step {draft.step_no} failed QA {len(existing)} times; a human must revise it"
                )
            version = 1 + max((m.message_version for m in existing), default=0)

            prospect = await uow.prospects.get(seq.prospect_id)
            if prospect is None:
                raise EntityNotFound("prospect", seq.prospect_id)
            thread = await uow.threads.get_or_create_for_sequence(seq.id, subject=draft.subject)
            claims: dict[str, Any] = {}
            for claim in draft.claims:
                merged = list(dict.fromkeys([*claims.get(claim.text, []), *claim.evidence_ids]))
                claims[claim.text] = [str(e) for e in merged]
            message = await uow.messages.create(
                sequence_id=seq.id,
                step_no=draft.step_no,
                subject=draft.subject,
                body=draft.body,
                idempotency_key=f"msg:{seq.id}:{draft.step_no}:{version}",
                claims=claims,
                thread_id=thread["id"],
                agent_run_id=prov.agent_run_id,
                message_version=version,
            )
            wanted = list(dict.fromkeys(e for c in draft.claims for e in c.evidence_ids))
            evidence = await uow.evidence.get_many(wanted) if wanted else []
            qa = run_qa(
                draft,
                prospect_id=prospect.id,
                evidence=evidence,
                previous_sent=await previously_sent_for_domain(uow, prospect.domain_id),
                similarity_threshold=self._threshold,
            )
            await uow.messages.record_qa(message.id, qa.as_record(prov.as_record()))
            target = MessageStatus.QA_PASSED if qa.passed else MessageStatus.QA_FAILED
            result = await self._transitions.transition_in(
                uow,
                message,
                target,
                actor=actor,
                reason="qa passed" if qa.passed else "qa failed: " + ",".join(qa.reason_codes),
                correlation_id=correlation_id,
            )
            return DraftOutcome(result.entity, qa, created=True)

    async def route_for_send(
        self, message_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_OUTREACH
    ) -> OutreachMessage:
        """QA_PASSED -> PENDING_APPROVAL (with a message_send approval request) when policy
        requires approval for sends, else -> APPROVED. The first QA-passed message of a PLANNED
        sequence also moves the sequence to READY. Idempotent on a message already routed."""
        async with self._uow_factory() as uow:
            message = await uow.messages.get(message_id)
            if message is None:
                raise EntityNotFound("outreach_message", message_id)
            if message.status is not MessageStatus.QA_PASSED:
                return message
            seq = await uow.sequences.get(message.sequence_id)
            assert seq is not None
            version = await uow.policies.active_version_for_campaign(seq.campaign_id)
            if version is None:
                raise PolicyUnavailable(f"no effective policy for campaign {seq.campaign_id}")
            if version.body.require_approval_for_send:
                await ApprovalService(self._uow_factory).create_in(
                    uow,
                    kind=ApprovalKind.MESSAGE_SEND,
                    subject_type="message",  # the subject type Lane B's guards look up
                    subject_id=message.id,
                    requested_by=actor,
                    reason=f"send approval for step {message.step_no}",
                    correlation_id=correlation_id,
                )
                target = MessageStatus.PENDING_APPROVAL
            else:
                target = MessageStatus.APPROVED
            moved = await self._transitions.transition_in(
                uow,
                message,
                target,
                actor=actor,
                reason="qa passed; routed for send",
                correlation_id=correlation_id,
            )
            await self._ready_sequence(uow, seq, actor, correlation_id)
            routed: OutreachMessage = moved.entity
            return routed

    async def _ready_sequence(
        self, uow: UnitOfWork, seq: OutreachSequence, actor: Actor, correlation_id: str
    ) -> None:
        if seq.status is SequenceStatus.PLANNED:
            await self._transitions.transition_in(
                uow,
                seq,
                SequenceStatus.READY,
                actor=actor,
                reason="first message passed QA",
                correlation_id=correlation_id,
            )
