"""C7: domain event and audit envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from kb_contracts.domain import DTO
from kb_contracts.enums import ActorType


class Actor(DTO):
    actor_type: ActorType
    actor_id: str


class DomainEvent(DTO):
    event_id: UUID
    type: str
    occurred_at: datetime
    correlation_id: str
    causation_id: str | None = None
    actor: Actor
    subject_type: str
    subject_id: UUID
    old_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
