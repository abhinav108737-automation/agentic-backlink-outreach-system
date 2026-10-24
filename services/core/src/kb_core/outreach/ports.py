"""Seams shared by the Lane E packages (outreach, replies, negotiation, verification).

Agents and handlers *produce* typed results; the deterministic services here persist them and ask
`TransitionPort` to move a state machine. Nothing in Lane E writes a `status` column itself.

`TransitionPort` is structurally `TransitionService.transition_in` (Lane B), which binds the
sequence, message, opportunity and backlink machines. Lane E depends on this seam, not on the
module. Terminal sequence states cancel jobs, cancel unsent messages and (OPTED_OUT, BOUNCED)
suppress the channel inside that service, so Lane E never duplicates those side effects.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from kb_contracts.domain import (
    Backlink,
    GuestPostOpportunity,
    OutreachMessage,
    OutreachSequence,
    Prospect,
    ScheduledJob,
)
from kb_contracts.enums import ActorType
from kb_contracts.events import Actor

from kb_core.db.protocols import UnitOfWork

JobHandler = Callable[[ScheduledJob], Awaitable[None]]
"""Same shape as the worker's handler type; declared locally so Lane E never imports scheduling."""

Transitionable = OutreachMessage | OutreachSequence | GuestPostOpportunity | Backlink | Prospect

# Versions recorded with every artefact so a result can be traced to the rules that made it.
QA_VERSION = "qa-1"
TEMPLATE_VERSION = "tmpl-guestpost-1"
CLASSIFIER_VERSION = "reply-rules-1"
VERIFIER_VERSION = "backlink-verifier-1"

ACTOR_OUTREACH = Actor(actor_type=ActorType.SYSTEM, actor_id="outreach")
ACTOR_REPLIES = Actor(actor_type=ActorType.SYSTEM, actor_id="replies")
ACTOR_NEGOTIATION = Actor(actor_type=ActorType.SYSTEM, actor_id="negotiation")
ACTOR_VERIFIER = Actor(actor_type=ActorType.SYSTEM, actor_id="backlink_verifier")


class TransitionOutcome(Protocol):
    @property
    def entity(self) -> Any: ...


class TransitionPort(Protocol):
    """Perform one guarded, audited, optimistically locked transition inside `uow`."""

    async def transition_in(
        self,
        uow: UnitOfWork,
        entity: Any,
        to_state: StrEnum,
        *,
        actor: Actor,
        reason: str,
        correlation_id: str,
        extras: Mapping[str, Any] | None = None,
    ) -> TransitionOutcome: ...


def sequence_key(sequence_id: object) -> str:
    """Concurrency key Lane B uses to serialise all jobs of one sequence."""
    return f"sequence:{sequence_id}"
