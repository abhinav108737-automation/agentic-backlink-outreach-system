"""Thin audit helper so every consequential action is attributable (docs/13 rule 4).

It writes through the *caller's* unit of work: the audit row therefore commits or rolls back
with the change it describes. It never opens its own transaction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from kb_contracts.enums import ActorType
from kb_contracts.events import Actor

from kb_core.db.protocols import AuditRepository

OPERATOR = Actor(actor_type=ActorType.OPERATOR, actor_id="operator")
SYSTEM_POLICY = Actor(actor_type=ActorType.SYSTEM, actor_id="policy_engine")
SYSTEM_QUALIFICATION = Actor(actor_type=ActorType.SYSTEM, actor_id="qualification")


class AuditTrail:
    def __init__(self, repo: AuditRepository) -> None:
        self._repo = repo

    async def record(
        self,
        *,
        actor: Actor,
        action: str,
        subject_type: str,
        subject_id: UUID | None,
        correlation_id: str,
        old_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        reason: str | None = None,
        policy_version_id: UUID | None = None,
        causation_id: str | None = None,
    ) -> UUID:
        return await self._repo.append(
            actor_type=actor.actor_type.value,
            actor_id=actor.actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=correlation_id,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
            policy_version_id=policy_version_id,
            causation_id=causation_id,
        )
