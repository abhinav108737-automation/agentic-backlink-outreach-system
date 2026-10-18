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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from kb_contracts.domain import Campaign, Prospect
from kb_contracts.enums import CampaignStatus, ProspectStatus
from kb_contracts.events import Actor, DomainEvent
from kb_contracts.providers import Clock
from kb_contracts.state_machines import CAMPAIGN, PROSPECT, StateMachine

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import OptimisticLockError, UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, InvalidInput
from kb_core.transitions.guards import Guard, GuardContext, GuardKey, default_guards

Reload = Callable[[UnitOfWork, UUID], Awaitable[Any | None]]
Write = Callable[[UnitOfWork, UUID, Any, int], Awaitable[Any]]

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
    entity: Any  # the updated Prospect | Campaign
    event: DomainEvent


async def _reload_prospect(uow: UnitOfWork, entity_id: UUID) -> Prospect | None:
    return await uow.prospects.get(entity_id)


async def _write_prospect(uow: UnitOfWork, entity_id: UUID, to: Any, version: int) -> Prospect:
    return await uow.prospects.set_status(entity_id, to, expected_version=version)


async def _reload_campaign(uow: UnitOfWork, entity_id: UUID) -> Campaign | None:
    return await uow.campaigns.get(entity_id)


async def _write_campaign(uow: UnitOfWork, entity_id: UUID, to: Any, version: int) -> Campaign:
    return await uow.campaigns.set_status(entity_id, to.value, expected_version=version)


_PROSPECT = _Binding(
    PROSPECT, "prospect", "prospects", ProspectStatus, _reload_prospect, _write_prospect
)
_CAMPAIGN = _Binding(
    CAMPAIGN, "campaign", "campaigns", CampaignStatus, _reload_campaign, _write_campaign
)


def _binding_for(entity: object) -> _Binding:
    if isinstance(entity, Prospect):
        return _PROSPECT
    if isinstance(entity, Campaign):
        return _CAMPAIGN
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
        entity: Prospect | Campaign,
        to_state: StrEnum,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
    ) -> TransitionResult:
        async with self._uow_factory() as uow:
            return await self.transition_in(
                uow, entity, to_state, actor=actor, reason=reason, correlation_id=correlation_id
            )

    async def transition_in(
        self,
        uow: UnitOfWork,
        entity: Prospect | Campaign,
        to_state: StrEnum,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
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

        updated = await bind.write(uow, entity.id, to_state, current.version)  # (c)

        if isinstance(updated, Prospect) and updated.status in _CANCEL_JOBS_ON:
            await uow.jobs.cancel_for_subject(subject_type="prospect", subject_id=updated.id)

        old = {"status": src.value, "version": current.version}
        new = {"status": to_state.value, "version": updated.version}
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
