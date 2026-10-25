"""Deliver one approved message through the injected `EmailProvider` (mock by default).

Lane B owns the *gates* and the *state moves* around a send (`begin_send` / `complete_send` /
`fail_send`: kill switch, pause, suppression, approval, real-send switch, sender validity,
duplicate step / contact, the three send limits, and the QUEUED -> SENDING -> SENT transitions).
Lane E owns the *delivery* in the middle and the work that follows it. Lane B's service is
injected through the structural `SendGate` protocol, so this module imports nothing of its.

Safety properties:

1. **Never twice.** `begin_send` commits QUEUED -> SENDING before this module calls the provider.
   A crash afterwards leaves the row in SENDING; on redelivery `begin_send` answers RECONCILE and
   the provider is asked whether the idempotency key already went out (`lookup_sent` when the
   provider offers it; otherwise a resend, which C5 guarantees is idempotent by key). A message
   already SENT / DELIVERED / BOUNCED is a no-op. A repeat `send` returns `duplicate=True`.
2. **Block handling.** BLOCKED / NOT_SENDABLE / ALREADY_SENT return normally (retrying cannot
   help). DEFERRED (transient block) raises `SendDeferred` so the queue retries with its bounded
   backoff.
3. **Bounded retries.** A retryable provider error (ProviderUnavailable, RateLimited) goes through
   `fail_send(retryable=True)`: RETRY_WAIT while attempts remain, then FAILED. Auth, configuration
   and malformed-response errors fail at once.
4. **After SENT.** The first message moves the prospect OUTREACH_READY -> IN_OUTREACH, and the
   next follow-up job is scheduled (`outreach.follow_up`, subject `sequence`, so a terminal
   sequence cancels it in the same transaction). Its `run_at` comes from the campaign policy
   (`kb_core.outreach.cadence`): `follow_up_delays_days[step - 1]` after the instant the provider
   accepted this message, moved into the sending window.
5. **Sending window.** When `begin_send` answers DEFERRED with `not_before` (the dispatch fell
   outside the window) the message stays QUEUED and a fresh `outreach.send` job is enqueued for
   that instant; this job then ends normally. Nothing is dropped and nothing goes early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from kb_contracts.domain import ContactChannel, OutreachMessage, OutreachSequence
from kb_contracts.enums import ProspectStatus
from kb_contracts.events import Actor
from kb_contracts.providers import (
    AuthRequired,
    Clock,
    EmailProvider,
    InvalidResponse,
    NotConfigured,
    OutboundEmail,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SendReceipt,
)

from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import DomainConflict, EntityNotFound, PolicyUnavailable
from kb_core.outreach.cadence import plan_after_send
from kb_core.outreach.payloads import (
    OUTREACH_FOLLOW_UP,
    OUTREACH_SEND,
    sequence_concurrency_key,
)
from kb_core.outreach.ports import ACTOR_OUTREACH, TransitionPort

_RETRYABLE = (ProviderUnavailable, RateLimited)
_TERMINAL_ERRORS = (AuthRequired, NotConfigured, InvalidResponse)


class SendDeferred(DomainConflict):
    """A transient policy block stops this send; the job should retry later (bounded)."""

    def __init__(self, codes: tuple[str, ...]) -> None:
        super().__init__("SEND_DEFERRED", "send deferred by policy: " + ", ".join(codes))
        self.codes = codes


class _BeginLike(Protocol):
    @property
    def outcome(self) -> str: ...
    @property
    def message(self) -> OutreachMessage: ...
    @property
    def attempt(self) -> int: ...
    @property
    def decision(self) -> Any: ...
    @property
    def not_before(self) -> datetime | None: ...


class SendGate(Protocol):
    """Structural view of Lane B's send service (`kb_core.sending.SendService`)."""

    async def begin_send(self, message_id: UUID, *, correlation_id: str) -> _BeginLike: ...

    async def complete_send(
        self, message_id: UUID, *, provider_message_id: str, correlation_id: str
    ) -> OutreachMessage: ...

    async def fail_send(
        self, message_id: UUID, *, attempt: int, retryable: bool, error: str, correlation_id: str
    ) -> OutreachMessage: ...


@runtime_checkable
class SentLookup(Protocol):
    def lookup_sent(self, idempotency_key: str) -> SendReceipt | None: ...


@dataclass(frozen=True)
class SendResult:
    outcome: str  # sent | already_sent | blocked | not_sendable | failed | deferred
    message: OutreachMessage
    provider_message_id: str | None = None
    duplicate: bool = False


async def load_channel(uow: UnitOfWork, seq: OutreachSequence) -> ContactChannel:
    prospect = await uow.prospects.get(seq.prospect_id)
    if prospect is None:
        raise EntityNotFound("prospect", seq.prospect_id)
    for ch in await uow.contacts.list_channels(prospect.domain_id):
        if ch.id == seq.contact_channel_id:
            return ch
    raise EntityNotFound("contact_channel", seq.contact_channel_id)


class DeliveryService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        email: EmailProvider,
        gate: SendGate,
        transitions: TransitionPort,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._email = email
        self._gate = gate
        self._tr = transitions
        self._clock = clock

    async def send(
        self, message_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_OUTREACH
    ) -> SendResult:
        begun = await self._gate.begin_send(message_id, correlation_id=correlation_id)
        outcome = str(begun.outcome)
        message = begun.message
        if outcome == "ALREADY_SENT":
            return SendResult("already_sent", message)
        if outcome == "BLOCKED":
            return SendResult("blocked", message)
        if outcome == "NOT_SENDABLE":
            return SendResult("not_sendable", message)
        if outcome == "DEFERRED":
            if begun.not_before is not None:
                await self._redispatch_at(message, begun.not_before, correlation_id)
                return SendResult("deferred", message)
            codes = tuple(c.value for c in begun.decision.block_codes) if begun.decision else ()
            raise SendDeferred(codes)

        recipient, thread_ref = await self._addressing(message)
        try:
            receipt = await self._deliver(
                message, recipient, thread_ref, reconcile=outcome == "RECONCILE"
            )
        except _RETRYABLE as exc:
            attempt = begun.attempt or 1
            failed = await self._gate.fail_send(
                message.id,
                attempt=attempt,
                retryable=True,
                error=type(exc).__name__,
                correlation_id=correlation_id,
            )
            if failed.status.value == "RETRY_WAIT":
                raise
            return SendResult("failed", failed)
        except (*_TERMINAL_ERRORS, ProviderError) as exc:
            failed = await self._gate.fail_send(
                message.id,
                attempt=begun.attempt or 1,
                retryable=False,
                error=type(exc).__name__,
                correlation_id=correlation_id,
            )
            return SendResult("failed", failed)

        sent = await self._gate.complete_send(
            message.id,
            provider_message_id=receipt.provider_message_id,
            correlation_id=correlation_id,
        )
        sent_at = self._clock.now()  # the previous successful send, the base of the next delay
        await self._after_sent(sent, sent_at, actor, correlation_id)
        return SendResult("sent", sent, receipt.provider_message_id, receipt.duplicate)

    async def _addressing(self, message: OutreachMessage) -> tuple[str, str | None]:
        async with self._uow_factory() as uow:
            seq = await uow.sequences.get(message.sequence_id)
            if seq is None:
                raise EntityNotFound("outreach_sequence", message.sequence_id)
            channel = await load_channel(uow, seq)
            thread = await uow.threads.get(message.thread_id) if message.thread_id else None
        thread_ref = (thread or {}).get("provider_thread_id") or (
            str(message.thread_id) if message.thread_id else None
        )
        return channel.value_normalized, thread_ref

    async def _deliver(
        self, message: OutreachMessage, recipient: str, thread_ref: str | None, *, reconcile: bool
    ) -> SendReceipt:
        if reconcile and isinstance(self._email, SentLookup):
            known = self._email.lookup_sent(message.idempotency_key)
            if known is not None:  # it left before the crash: never send it again
                return known.model_copy(update={"duplicate": True})
        return await self._email.send(
            OutboundEmail(
                idempotency_key=message.idempotency_key,
                to=recipient,
                subject=message.subject,
                body=message.body,
                thread_ref=thread_ref,
            )
        )

    async def _redispatch_at(
        self, message: OutreachMessage, not_before: datetime, correlation_id: str
    ) -> None:
        """Re-enqueue the dispatch for the next in-window instant. Idempotent per instant."""
        async with self._uow_factory() as uow:
            await uow.jobs.enqueue(
                job_type=OUTREACH_SEND,
                payload={
                    "message_id": str(message.id),
                    "sequence_id": str(message.sequence_id),
                    "correlation_id": correlation_id,
                },
                run_at=not_before,
                idempotency_key=f"{OUTREACH_SEND}:{message.id}:{not_before.isoformat()}",
                concurrency_key=sequence_concurrency_key(message.sequence_id),
                subject_type="sequence",
                subject_id=message.sequence_id,
            )

    async def _after_sent(
        self, sent: OutreachMessage, sent_at: datetime, actor: Actor, correlation_id: str
    ) -> None:
        async with self._uow_factory() as uow:
            seq = await uow.sequences.get(sent.sequence_id)
            assert seq is not None
            prospect = await uow.prospects.get(seq.prospect_id)
            if prospect is not None and prospect.status is ProspectStatus.OUTREACH_READY:
                await self._tr.transition_in(
                    uow,
                    prospect,
                    ProspectStatus.IN_OUTREACH,
                    actor=actor,
                    reason="first message sent",
                    correlation_id=correlation_id,
                )
            version = await uow.policies.active_version_for_campaign(seq.campaign_id)
            if version is None:
                raise PolicyUnavailable(f"no effective policy for campaign {seq.campaign_id}")
            plan = plan_after_send(version.body, sent_step_no=sent.step_no, sent_at=sent_at)
            if plan is None:
                await uow.sequences.set_next_due(seq.id, run_at=None)
                return
            await uow.sequences.set_next_due(seq.id, run_at=plan.run_at)
            await uow.jobs.enqueue(
                job_type=OUTREACH_FOLLOW_UP,
                payload={
                    "sequence_id": str(seq.id),
                    "step_no": plan.step_no,
                    "correlation_id": correlation_id,
                },
                run_at=plan.run_at,
                idempotency_key=f"{OUTREACH_FOLLOW_UP}:{seq.id}:{plan.step_no}",
                concurrency_key=sequence_concurrency_key(seq.id),
                subject_type="sequence",
                subject_id=seq.id,
            )
