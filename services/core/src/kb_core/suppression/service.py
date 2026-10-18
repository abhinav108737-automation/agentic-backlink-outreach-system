"""Suppression: hard block, never bypassable, no delete (only supersede).

There is deliberately no `force`, `override` or `skip_suppression` parameter anywhere in this
package or in `PolicyEngine`. A suppressed address, domain or contact stops every outbound action
until an entry is explicitly superseded, and that supersession is itself audited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from kb_contracts.domain import SuppressionEntry
from kb_contracts.enums import SuppressionReason, SuppressionScope
from kb_contracts.events import Actor

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import UnitOfWork
from kb_core.errors import EntityNotFound
from kb_core.suppression.normalize import normalize_domain, normalize_email, normalize_value


@dataclass(frozen=True)
class SuppressionCheck:
    suppressed: bool
    entries: Sequence[SuppressionEntry]

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted({e.scope.value for e in self.entries}))


async def check_suppression(
    uow: UnitOfWork,
    *,
    campaign_id: UUID | None,
    email: str | None = None,
    domain: str | None = None,
    contact_id: UUID | str | None = None,
) -> SuppressionCheck:
    """Match every identifier supplied. An email also checks its own domain, so a domain-level
    suppression blocks every address at that domain. Global entries always apply; campaign-scoped
    entries apply to that campaign."""
    values: dict[SuppressionScope, str] = {}
    if email:
        values[SuppressionScope.EMAIL] = normalize_email(email)
        values[SuppressionScope.DOMAIN] = normalize_domain(email)
    if domain:
        values[SuppressionScope.DOMAIN] = normalize_domain(domain)
    if contact_id is not None:
        values[SuppressionScope.CONTACT] = str(contact_id).strip().lower()
    if not values:
        return SuppressionCheck(False, ())
    entries = await uow.suppressions.matching(values=values, campaign_id=campaign_id)
    return SuppressionCheck(bool(entries), tuple(entries))


class SuppressionService:
    """Mutations only; reads go through `check_suppression` inside the caller's unit of work."""

    async def add(
        self,
        uow: UnitOfWork,
        *,
        scope: SuppressionScope,
        value: str,
        reason: SuppressionReason,
        actor: Actor,
        correlation_id: str,
        campaign_id: UUID | None = None,
        source_event_ref: str | None = None,
    ) -> SuppressionEntry:
        normalized = normalize_value(scope, value)
        entry = await uow.suppressions.add(
            scope=scope,
            value_normalized=normalized,
            reason=reason.value,
            campaign_id=campaign_id,
            created_by=f"{actor.actor_type.value}:{actor.actor_id}",
            source_event_ref=source_event_ref,
        )
        await AuditTrail(uow.audit).record(
            actor=actor,
            action="suppression.added",
            subject_type="suppression_entry",
            subject_id=entry.id,
            correlation_id=correlation_id,
            new_state={
                "scope": scope.value,
                "value": normalized,
                "reason": reason.value,
                "campaign_id": str(campaign_id) if campaign_id else None,
            },
            reason=reason.value,
        )
        return entry

    async def supersede(
        self, uow: UnitOfWork, *, entry_id: UUID, actor: Actor, correlation_id: str, reason: str
    ) -> SuppressionEntry:
        try:
            entry = await uow.suppressions.supersede(
                entry_id, superseded_by=f"{actor.actor_type.value}:{actor.actor_id}"
            )
        except LookupError as exc:
            raise EntityNotFound("suppression_entry", entry_id) from exc
        await AuditTrail(uow.audit).record(
            actor=actor,
            action="suppression.superseded",
            subject_type="suppression_entry",
            subject_id=entry.id,
            correlation_id=correlation_id,
            reason=reason,
        )
        return entry
