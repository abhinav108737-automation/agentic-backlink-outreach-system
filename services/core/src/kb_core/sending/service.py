"""The outreach send path: approve -> enqueue -> (worker) begin_send -> complete/fail.

Everything here is deterministic and goes through `TransitionService`; no function sends
anything. Delivery is the `outreach.send` handler's job (Lane E): it calls `begin_send`, hands
the message to the provider using `message.idempotency_key`, then calls `complete_send` or
`fail_send`.

Policy runs twice on purpose (docs/13 2b): at enqueue (`enqueue_send`) and again, from fresh
state, immediately before the message moves to SENDING (`begin_send`). A suppression, kill
switch or pause that appears after approval therefore still stops the send.

Block handling. A block is PERMANENT when the cause cannot heal for this exact message
(suppression, duplicate step/contact, unsupported claim, failed thresholds): the message goes to
POLICY_BLOCKED (terminal; a new version is drafted instead). Anything else is TRANSIENT (kill
switch, pause, send limit, sender, approval, real-send flag): the message keeps its state, the
caller gets the codes, and a later attempt re-evaluates.

Crash safety. `begin_send` never re-enters SENDING: a message already SENDING is reported as
RECONCILE and the handler must look the idempotency key up at the provider before any resend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from kb_contracts.domain import OutreachMessage, OutreachSequence
from kb_contracts.enums import (
    ActorType,
    ApprovalKind,
    ApprovalStatus,
    MessageStatus,
    SequenceStatus,
)
from kb_contracts.events import Actor
from kb_contracts.policy import BlockCode, PolicyCheckRequest, PolicyDecision
from kb_contracts.providers import Clock
from kb_contracts.state_machines import OUTREACH_MESSAGE

from kb_core.approvals.service import ApprovalService
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import (
    EntityNotFound,
    IdempotencyKeyReused,
    InvalidInput,
    PolicyBlocked,
    PolicyUnavailable,
)
from kb_core.idempotency.service import IdempotencyService, request_hash
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.scheduling.jobs import SEQUENCE_SUBJECT, SendPayload, enqueue_send
from kb_core.scheduling.queue import InUowQueue
from kb_core.transitions.service import TransitionService

SYSTEM_SEND = Actor(actor_type=ActorType.SYSTEM, actor_id="send_service")
MAX_SEND_ATTEMPTS = 3
SCOPE_APPROVE = "message.approve"
SCOPE_SEND = "message.send"

# Causes that cannot heal for this exact message.
PERMANENT_BLOCKS = frozenset(
    {
        BlockCode.SUPPRESSED,
        BlockCode.DUPLICATE_STEP,
        BlockCode.DUPLICATE_CONTACT,
        BlockCode.UNSUPPORTED_CLAIM,
        BlockCode.HARD_THRESHOLD_FAILED,
        BlockCode.METRIC_MISSING,
    }
)
_SENT = frozenset({MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED})
_SEQUENCE_TERMINAL = frozenset(
    {
        SequenceStatus.REPLIED,
        SequenceStatus.OPTED_OUT,
        SequenceStatus.BOUNCED,
        SequenceStatus.CANCELLED,
        SequenceStatus.COMPLETED_NO_REPLY,
    }
)


class BeginOutcome(StrEnum):
    PROCEED = "PROCEED"  # now SENDING: hand to the provider with message.idempotency_key
    RECONCILE = "RECONCILE"  # found SENDING: ask the provider about the key; never resend blind
    ALREADY_SENT = "ALREADY_SENT"  # SENT/DELIVERED/BOUNCED: nothing to do
    BLOCKED = "BLOCKED"  # permanent block; message is now POLICY_BLOCKED
    DEFERRED = "DEFERRED"  # transient block; message left QUEUED; retry later
    NOT_SENDABLE = "NOT_SENDABLE"  # cancelled/terminal/not yet queued, or sequence is terminal


@dataclass(frozen=True)
class BeginSend:
    outcome: BeginOutcome
    message: OutreachMessage
    decision: PolicyDecision | None = None
    attempt: int = 0


@dataclass(frozen=True)
class EnqueuedSend:
    message: OutreachMessage
    job_id: UUID
    replayed: bool


def _context(message: OutreachMessage) -> dict[str, str]:
    return {"delivery_mode": message.delivery_mode.value}


class SendService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: DeterministicPolicyEngine,
        transitions: TransitionService,
        approvals: ApprovalService,
        clock: Clock,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._transitions = transitions
        self._approvals = approvals
        self._clock = clock
        self._idem = idempotency or IdempotencyService()

    # -- loading ---------------------------------------------------------------------------
    @staticmethod
    async def _load(uow: UnitOfWork, message_id: UUID) -> tuple[OutreachMessage, OutreachSequence]:
        message = await uow.messages.get(message_id)
        if message is None:
            raise EntityNotFound("message", message_id)
        sequence = await uow.sequences.get(message.sequence_id)
        if sequence is None:
            raise EntityNotFound("outreach_sequence", message.sequence_id)
        return message, sequence

    def _request(
        self, action: str, message: OutreachMessage, seq: OutreachSequence, correlation_id: str
    ) -> PolicyCheckRequest:
        return PolicyCheckRequest(
            action=action,
            campaign_id=seq.campaign_id,
            subject_type="message",
            subject_id=message.id,
            context=_context(message),
            correlation_id=correlation_id,
        )

    # -- approval --------------------------------------------------------------------------
    async def approve(
        self,
        message_id: UUID,
        *,
        decision: ApprovalStatus,
        actor: Actor,
        reason: str | None,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[OutreachMessage, bool]:
        """Operator decision on a message. Approval request, decision and the message transition
        commit together. Returns (message, replayed)."""
        if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise InvalidInput("a message is decided as APPROVED or REJECTED")
        async with self._uow_factory() as uow:

            async def op() -> str:
                done = await self._approve_in(
                    uow, message_id, decision, actor, reason, correlation_id
                )
                return f"message:{done.id}"

            if idempotency_key is None:
                return await self._approve_in(
                    uow, message_id, decision, actor, reason, correlation_id
                ), False
            outcome = await self._idem.execute(
                uow,
                scope=SCOPE_APPROVE,
                key=idempotency_key,
                payload={"message_id": str(message_id), "decision": decision.value},
                operation=op,
            )
            current = await uow.messages.get(UUID(outcome.ref.split(":", 1)[1]))
            if current is None:  # pragma: no cover - ref written in the same history
                raise EntityNotFound("message", message_id)
            return current, outcome.replayed

    async def _approve_in(
        self,
        uow: UnitOfWork,
        message_id: UUID,
        decision: ApprovalStatus,
        actor: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> OutreachMessage:
        message, seq = await self._load(uow, message_id)
        target = (
            MessageStatus.APPROVED
            if decision is ApprovalStatus.APPROVED
            else MessageStatus.REJECTED
        )
        why = reason or f"operator {decision.value.lower()} the message"

        if message.status is MessageStatus.QA_PASSED:
            version = await uow.policies.active_version_for_campaign(seq.campaign_id)
            if version is None:
                raise PolicyUnavailable(f"no effective policy for campaign {seq.campaign_id}")
            if decision is ApprovalStatus.APPROVED and not version.body.require_approval_for_send:
                res = await self._transitions.transition_in(
                    uow, message, MessageStatus.APPROVED,
                    actor=actor, reason=why, correlation_id=correlation_id,
                )  # fmt: skip
                return _msg(res.entity)
            moved = await self._transitions.transition_in(
                uow, message, MessageStatus.PENDING_APPROVAL,
                actor=actor, reason="approval requested", correlation_id=correlation_id,
            )  # fmt: skip
            message = _msg(moved.entity)
        elif message.status is not MessageStatus.PENDING_APPROVAL:
            OUTREACH_MESSAGE.check(message.status, target)  # InvalidTransition -> 409
            raise InvalidInput("message is not awaiting a decision")  # pragma: no cover

        latest = await uow.approvals.latest_for_subject(
            subject_type="message", subject_id=message.id, kind=ApprovalKind.MESSAGE_SEND.value
        )
        if latest is None or latest.status is not ApprovalStatus.PENDING:
            latest = await self._approvals.create_in(
                uow,
                kind=ApprovalKind.MESSAGE_SEND,
                subject_type="message",
                subject_id=message.id,
                requested_by=actor,
                reason=reason,
                correlation_id=correlation_id,
            )
        await self._approvals.decide_in(
            uow, latest.id, status=decision, decided_by=actor, reason=reason,
            correlation_id=correlation_id,
        )  # fmt: skip
        res = await self._transitions.transition_in(
            uow, message, target, actor=actor, reason=why, correlation_id=correlation_id
        )
        return _msg(res.entity)

    # -- enqueue ---------------------------------------------------------------------------
    async def enqueue_send(
        self,
        message_id: UUID,
        *,
        actor: Actor,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> EnqueuedSend:
        """Policy re-check, APPROVED -> QUEUED and the `outreach.send` job in ONE transaction.
        Never sends. A block raises `PolicyBlocked` after the block (and any permanent
        POLICY_BLOCKED transition) has been committed."""
        payload = {"message_id": str(message_id)}
        if idempotency_key is not None:
            replay = await self._peek(idempotency_key, payload)
            if replay is not None:
                return replay

        blocked: PolicyDecision | None = None
        async with self._uow_factory() as uow:
            message, seq = await self._load(uow, message_id)
            if message.status is not MessageStatus.QUEUED:
                OUTREACH_MESSAGE.check(message.status, MessageStatus.QUEUED)
            decision = await self._policy.check_in(
                uow, self._request("enqueue_send", message, seq, correlation_id)
            )
            if not decision.allowed:
                await self._apply_block(uow, message, decision, actor, correlation_id)
                blocked = decision
        if blocked is not None:
            raise PolicyBlocked(blocked)

        async with self._uow_factory() as uow:

            async def op() -> str:
                job_id, _ = await self._queue_in(uow, message_id, actor, correlation_id)
                return f"job:{job_id}:{message_id}"

            if idempotency_key is None:
                job_id, message = await self._queue_in(uow, message_id, actor, correlation_id)
                return EnqueuedSend(message, job_id, replayed=False)
            outcome = await self._idem.execute(
                uow, scope=SCOPE_SEND, key=idempotency_key, payload=payload, operation=op
            )
            job_id = UUID(outcome.ref.split(":")[1])
            current = await uow.messages.get(message_id)
            if current is None:  # pragma: no cover
                raise EntityNotFound("message", message_id)
            return EnqueuedSend(current, job_id, outcome.replayed)

    async def _peek(self, key: str, payload: dict[str, str]) -> EnqueuedSend | None:
        async with self._uow_factory() as uow:
            existing = await uow.idempotency.get(scope=SCOPE_SEND, key=key)
            if existing is None or not existing.get("response_ref"):
                return None
            if existing["request_hash"] != request_hash(payload):
                raise IdempotencyKeyReused(
                    f"Idempotency-Key {key!r} was used with a different request"
                )
            _, job, message_id = str(existing["response_ref"]).split(":")
            current = await uow.messages.get(UUID(message_id))
            if current is None:  # pragma: no cover
                raise EntityNotFound("message", message_id)
            return EnqueuedSend(current, UUID(job), replayed=True)

    async def _queue_in(
        self, uow: UnitOfWork, message_id: UUID, actor: Actor, correlation_id: str
    ) -> tuple[UUID, OutreachMessage]:
        message, seq = await self._load(uow, message_id)
        if message.status is not MessageStatus.QUEUED:
            res = await self._transitions.transition_in(
                uow, message, MessageStatus.QUEUED,
                actor=actor, reason="send requested", correlation_id=correlation_id,
            )  # fmt: skip
            message = _msg(res.entity)
        job_id = await enqueue_send(
            InUowQueue(uow),
            SendPayload(message_id=message.id, sequence_id=seq.id, correlation_id=correlation_id),
            self._clock.now(),
        )
        return UUID(job_id), message

    async def _apply_block(
        self,
        uow: UnitOfWork,
        message: OutreachMessage,
        decision: PolicyDecision,
        actor: Actor,
        correlation_id: str,
    ) -> bool:
        """True when the block was permanent and the message is now POLICY_BLOCKED."""
        if not PERMANENT_BLOCKS.intersection(decision.block_codes):
            return False
        await self._transitions.transition_in(
            uow, message, MessageStatus.POLICY_BLOCKED,
            actor=actor,
            reason="policy blocked: " + ", ".join(decision.block_codes),
            correlation_id=correlation_id,
        )  # fmt: skip
        return True

    # -- worker side -----------------------------------------------------------------------
    async def begin_send(self, message_id: UUID, *, correlation_id: str) -> BeginSend:
        """The pre-send gate. Call at the top of the `outreach.send` handler."""
        async with self._uow_factory() as uow:
            message, seq = await self._load(uow, message_id)
            if message.status in _SENT:
                return BeginSend(BeginOutcome.ALREADY_SENT, message)
            if message.status is MessageStatus.SENDING:
                return BeginSend(BeginOutcome.RECONCILE, message)
            if (
                message.status not in (MessageStatus.QUEUED, MessageStatus.RETRY_WAIT)
                or seq.status in _SEQUENCE_TERMINAL
            ):
                return BeginSend(BeginOutcome.NOT_SENDABLE, message)
            if message.status is MessageStatus.RETRY_WAIT:
                res = await self._transitions.transition_in(
                    uow, message, MessageStatus.QUEUED,
                    actor=SYSTEM_SEND, reason="retry", correlation_id=correlation_id,
                )  # fmt: skip
                message = _msg(res.entity)

            decision = await self._policy.check_in(
                uow, self._request("send", message, seq, correlation_id)
            )
            if not decision.allowed:
                if await self._apply_block(uow, message, decision, SYSTEM_SEND, correlation_id):
                    return BeginSend(BeginOutcome.BLOCKED, message, decision)
                return BeginSend(BeginOutcome.DEFERRED, message, decision)

            attempt = await uow.messages.bump_attempts(message.id)
            res = await self._transitions.transition_in(
                uow, message, MessageStatus.SENDING,
                actor=SYSTEM_SEND, reason="pre-send policy check passed",
                correlation_id=correlation_id,
            )  # fmt: skip
            return BeginSend(BeginOutcome.PROCEED, _msg(res.entity), decision, attempt)

    async def complete_send(
        self, message_id: UUID, *, provider_message_id: str, correlation_id: str
    ) -> OutreachMessage:
        """SENDING -> SENT with the provider id, and the sequence moves to WAITING_REPLY."""
        if not provider_message_id.strip():
            raise InvalidInput("provider_message_id must not be empty")
        async with self._uow_factory() as uow:
            message, seq = await self._load(uow, message_id)
            if message.status in _SENT:  # a repeated completion is a no-op, never a re-send
                return message
            res = await self._transitions.transition_in(
                uow, message, MessageStatus.SENT,
                actor=SYSTEM_SEND, reason="provider accepted the message",
                correlation_id=correlation_id,
                extras={"provider_message_id": provider_message_id},
            )  # fmt: skip
            if seq.status in (SequenceStatus.READY, SequenceStatus.FOLLOW_UP_DUE):
                await self._transitions.transition_in(
                    uow, seq, SequenceStatus.WAITING_REPLY,
                    actor=SYSTEM_SEND, reason=f"step {message.step_no} sent",
                    correlation_id=correlation_id,
                )  # fmt: skip
                await uow.sequences.advance_step(seq.id)
            return _msg(res.entity)

    async def fail_send(
        self,
        message_id: UUID,
        *,
        attempt: int,
        retryable: bool,
        error: str,
        correlation_id: str,
    ) -> OutreachMessage:
        """SENDING -> RETRY_WAIT while attempts remain and the failure is retryable, else FAILED."""
        async with self._uow_factory() as uow:
            message, _ = await self._load(uow, message_id)
            to = (
                MessageStatus.RETRY_WAIT
                if retryable and attempt < MAX_SEND_ATTEMPTS
                else MessageStatus.FAILED
            )
            res = await self._transitions.transition_in(
                uow, message, to, actor=SYSTEM_SEND,
                reason=f"send failed (attempt {attempt}): {error[:150]}",
                correlation_id=correlation_id,
            )  # fmt: skip
            return _msg(res.entity)


def _msg(entity: object) -> OutreachMessage:
    assert isinstance(entity, OutreachMessage)
    return entity


__all__ = [
    "MAX_SEND_ATTEMPTS",
    "PERMANENT_BLOCKS",
    "SEQUENCE_SUBJECT",
    "SYSTEM_SEND",
    "BeginOutcome",
    "BeginSend",
    "EnqueuedSend",
    "SendService",
]
