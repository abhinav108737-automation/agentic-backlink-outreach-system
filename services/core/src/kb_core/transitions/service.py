"""`TransitionService`: the only writer of `status` columns (docs/13 enforcement rules).

`transition()` performs, in this order and inside ONE unit of work:
  (a) reject any (from, to) pair not in the frozen table -> `InvalidTransition`
  (b) evaluate guards                                      -> `GuardFailed`
  (c) bump `version` optimistically                        -> `OptimisticLockError`
  (d) write the `audit_events` row                         (same transaction)
  (e) return a domain event
A failure at any step raises out of the `async with`, and the unit of work rolls back, so a
status change without its audit row (or the reverse) cannot exist.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from kb_contracts.domain import (
    Backlink,
    Campaign,
    GuestPostOpportunity,
    OutreachMessage,
    OutreachSequence,
    Prospect,
)
from kb_contracts.enums import (
    BacklinkStatus,
    CampaignStatus,
    MessageStatus,
    OpportunityStatus,
    ProspectStatus,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.events import Actor, DomainEvent
from kb_contracts.providers import Clock
from kb_contracts.state_machines import (
    BACKLINK,
    CAMPAIGN,
    OPPORTUNITY,
    OUTREACH_MESSAGE,
    OUTREACH_SEQUENCE,
    PROSPECT,
    StateMachine,
)

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import OptimisticLockError, UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, GuardFailed, InvalidInput
from kb_core.suppression.service import SuppressionService
from kb_core.transitions.guards import Guard, GuardContext, GuardKey, default_guards

Transitionable = (
    Prospect | Campaign | OutreachSequence | OutreachMessage | GuestPostOpportunity | Backlink
)
Reload = Callable[[UnitOfWork, UUID], Awaitable[Any | None]]
Write = Callable[[UnitOfWork, UUID, Any, int, Mapping[str, Any]], Awaitable[Any]]

# Entering one of these ends the sequence: its pending jobs and unsent messages are cancelled
# in the same transaction (docs/13 2a/2b).
SEQUENCE_TERMINAL = frozenset(
    {
        SequenceStatus.REPLIED,
        SequenceStatus.OPTED_OUT,
        SequenceStatus.BOUNCED,
        SequenceStatus.CANCELLED,
        SequenceStatus.COMPLETED_NO_REPLY,
    }
)
# Not-yet-sent work. SENDING is deliberately absent: it is in flight and is reconciled with the
# provider by idempotency key, never cancelled out from under the send.
_UNSENT_MESSAGE = frozenset(
    {
        MessageStatus.DRAFT,
        MessageStatus.QA_FAILED,
        MessageStatus.QA_PASSED,
        MessageStatus.PENDING_APPROVAL,
        MessageStatus.APPROVED,
        MessageStatus.QUEUED,
        MessageStatus.RETRY_WAIT,
    }
)
_SUPPRESS_ON = {
    SequenceStatus.OPTED_OUT: SuppressionReason.OPT_OUT,
    SequenceStatus.BOUNCED: SuppressionReason.BOUNCE,
}

# Entering one of these ends pending automated work for the prospect (docs/13 2a, same txn).
_CANCEL_JOBS_ON = frozenset(
    {
        ProspectStatus.SUPPRESSED,
        ProspectStatus.REJECTED,
        ProspectStatus.LOST,
        ProspectStatus.WON,
    }
)


@dataclass(frozen=True)
class _Binding:
    machine: StateMachine[Any]
    subject_type: str
    table: str
    enum: type[StrEnum]
    reload: Reload
    write: Write


@dataclass(frozen=True)
class TransitionResult:
    entity: Any  # the updated aggregate
    event: DomainEvent


async def _reload_prospect(uow: UnitOfWork, entity_id: UUID) -> Prospect | None:
    return await uow.prospects.get(entity_id)


async def _write_prospect(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> Prospect:
    return await uow.prospects.set_status(entity_id, to, expected_version=version)


async def _reload_campaign(uow: UnitOfWork, entity_id: UUID) -> Campaign | None:
    return await uow.campaigns.get(entity_id)


async def _write_campaign(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> Campaign:
    return await uow.campaigns.set_status(entity_id, to.value, expected_version=version)


async def _reload_sequence(uow: UnitOfWork, entity_id: UUID) -> OutreachSequence | None:
    return await uow.sequences.get(entity_id)


async def _write_sequence(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> OutreachSequence:
    return await uow.sequences.set_status(entity_id, to, expected_version=version)


async def _reload_message(uow: UnitOfWork, entity_id: UUID) -> OutreachMessage | None:
    return await uow.messages.get(entity_id)


async def _write_message(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> OutreachMessage:
    if to is MessageStatus.SENT:  # the provider id is part of the SENDING -> SENT transition
        provider_id = extras.get("provider_message_id")
        if not provider_id:
            raise InvalidInput("SENT requires the provider_message_id")
        return await uow.messages.mark_sent(
            entity_id, provider_message_id=str(provider_id), expected_version=version
        )
    return await uow.messages.set_status(entity_id, to, expected_version=version)


async def _reload_opportunity(uow: UnitOfWork, entity_id: UUID) -> GuestPostOpportunity | None:
    return await uow.opportunities.get(entity_id)


async def _write_opportunity(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> GuestPostOpportunity:
    return await uow.opportunities.set_status(entity_id, to, expected_version=version)


async def _reload_backlink(uow: UnitOfWork, entity_id: UUID) -> Backlink | None:
    return await uow.backlinks.get(entity_id)


async def _write_backlink(
    uow: UnitOfWork, entity_id: UUID, to: Any, version: int, extras: Mapping[str, Any]
) -> Backlink:
    return await uow.backlinks.set_status(entity_id, to, expected_version=version)


_PROSPECT = _Binding(
    PROSPECT, "prospect", "prospects", ProspectStatus, _reload_prospect, _write_prospect
)
_CAMPAIGN = _Binding(
    CAMPAIGN, "campaign", "campaigns", CampaignStatus, _reload_campaign, _write_campaign
)
_SEQUENCE = _Binding(
    OUTREACH_SEQUENCE,
    "outreach_sequence",
    "outreach_sequences",
    SequenceStatus,
    _reload_sequence,
    _write_sequence,
)
_MESSAGE = _Binding(
    OUTREACH_MESSAGE,
    "outreach_message",
    "outreach_messages",
    MessageStatus,
    _reload_message,
    _write_message,
)
_OPPORTUNITY = _Binding(
    OPPORTUNITY,
    "opportunity",
    "guest_post_opportunities",
    OpportunityStatus,
    _reload_opportunity,
    _write_opportunity,
)
_BACKLINK = _Binding(
    BACKLINK, "backlink", "backlinks", BacklinkStatus, _reload_backlink, _write_backlink
)


def _binding_for(entity: object) -> _Binding:
    if isinstance(entity, Prospect):
        return _PROSPECT
    if isinstance(entity, Campaign):
        return _CAMPAIGN
    if isinstance(entity, OutreachSequence):
        return _SEQUENCE
    if isinstance(entity, OutreachMessage):
        return _MESSAGE
    if isinstance(entity, GuestPostOpportunity):
        return _OPPORTUNITY
    if isinstance(entity, Backlink):
        return _BACKLINK
    raise InvalidInput(f"no transition machine is bound to {type(entity).__name__}")


class TransitionService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        guards: dict[GuardKey, tuple[Guard, ...]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._guards = guards if guards is not None else default_guards()

    async def transition(
        self,
        entity: Transitionable,
        to_state: StrEnum,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
        extras: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        async with self._uow_factory() as uow:
            return await self.transition_in(
                uow,
                entity,
                to_state,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
                extras=extras,
            )

    async def transition_in(
        self,
        uow: UnitOfWork,
        entity: Transitionable,
        to_state: StrEnum,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
        extras: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        """Same as `transition` but inside the caller's unit of work, so a caller can make a
        transition atomic with its own writes (for example a qualification decision)."""
        bind = _binding_for(entity)
        if not isinstance(to_state, bind.enum):
            raise InvalidInput(f"{to_state!r} is not a {bind.machine.name} state")
        if not reason.strip():
            raise InvalidInput("a transition requires a reason")

        current = await bind.reload(uow, entity.id)
        if current is None:
            raise EntityNotFound(bind.subject_type, entity.id)
        if current.version != entity.version:  # stale caller view: never guard on old data
            raise OptimisticLockError(bind.table, entity.id, entity.version)
        src: StrEnum = current.status

        bind.machine.check(src, to_state)  # (a)

        ctx = GuardContext(
            uow=uow,
            entity=current,
            src=src,
            dst=to_state,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
            now=self._clock.now(),
        )
        for key in (
            (bind.machine.name, src.value, to_state.value),
            (bind.machine.name, None, to_state.value),
        ):
            for guard in self._guards.get(key, ()):  # (b)
                await guard(ctx)

        updated = await bind.write(uow, entity.id, to_state, current.version, extras or {})  # (c)

        if isinstance(updated, Prospect) and updated.status in _CANCEL_JOBS_ON:
            await uow.jobs.cancel_for_subject(subject_type="prospect", subject_id=updated.id)

        old = {"status": src.value, "version": current.version}
        new = {"status": to_state.value, "version": updated.version}
        if isinstance(updated, OutreachSequence) and updated.status in SEQUENCE_TERMINAL:
            await self._on_sequence_terminal(
                uow, updated, actor=actor, reason=reason, correlation_id=correlation_id
            )
        audit_id = await AuditTrail(uow.audit).record(  # (d)
            actor=actor,
            action=f"{bind.subject_type}.transition",
            subject_type=bind.subject_type,
            subject_id=entity.id,
            correlation_id=correlation_id,
            old_state=old,
            new_state=new,
            reason=reason,
        )
        event = DomainEvent(  # (e)
            event_id=audit_id,
            type=f"{bind.subject_type}.transitioned",
            occurred_at=self._clock.now(),
            correlation_id=correlation_id,
            actor=actor,
            subject_type=bind.subject_type,
            subject_id=entity.id,
            old_state=old,
            new_state=new,
            reason=reason,
        )
        return TransitionResult(entity=updated, event=event)

    async def _on_sequence_terminal(
        self,
        uow: UnitOfWork,
        seq: OutreachSequence,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
    ) -> None:
        """Runs inside the transition's transaction: any failure here rolls the status change
        back too. Order: cancel jobs, cancel unsent messages, then suppress."""
        await uow.jobs.cancel_for_subject(subject_type="sequence", subject_id=seq.id)
        for message in await uow.messages.list_for_sequence(seq.id):
            if message.status in _UNSENT_MESSAGE:
                await self.transition_in(
                    uow,
                    message,
                    MessageStatus.CANCELLED,
                    actor=actor,
                    reason=f"sequence entered {seq.status.value}",
                    correlation_id=correlation_id,
                )
        suppress_reason = _SUPPRESS_ON.get(seq.status)
        if suppress_reason is not None:
            await self._suppress_channel(
                uow, seq, suppress_reason, actor=actor, reason=reason, correlation_id=correlation_id
            )

    @staticmethod
    async def _suppress_channel(
        uow: UnitOfWork,
        seq: OutreachSequence,
        why: SuppressionReason,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
    ) -> None:
        prospect = await uow.prospects.get(seq.prospect_id)
        if prospect is None:
            raise EntityNotFound("prospect", seq.prospect_id)
        channel = next(
            (c for c in await uow.contacts.list_channels(prospect.domain_id)
             if c.id == seq.contact_channel_id),
            None,
        )  # fmt: skip
        if channel is None:
            raise GuardFailed(
                "CONTACT_CHANNEL_MISSING", "cannot suppress: the sequence's channel is unknown"
            )
        if channel.channel_type == "email":
            scope, value = SuppressionScope.EMAIL, channel.value_normalized
        else:
            scope, value = SuppressionScope.CONTACT, str(channel.contact_id)
        await SuppressionService().add(  # global: an opt-out or a bounce holds in every campaign
            uow,
            scope=scope,
            value=value,
            reason=why,
            actor=actor,
            correlation_id=correlation_id,
            campaign_id=None,
            source_event_ref=f"outreach_sequence:{seq.id}:{reason}"[:200],
        )
