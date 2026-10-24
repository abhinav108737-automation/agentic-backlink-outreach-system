"""Guest-post opportunity lifecycle after agreement, and the PUBLISHED -> EXPECTED hand-off.

AGREED -> CONTENT_IN_PROGRESS -> CONTENT_SUBMITTED (-> REVISION_REQUESTED -> CONTENT_SUBMITTED)
-> PUBLISHED. Every submission is a new `content_submissions` row with an allocated `version_no`.

Reaching PUBLISHED creates the `backlinks` row in EXPECTED in the same transaction (docs/13 3):
either both exist or neither does, and PUBLISHED is terminal so a redelivered call cannot create
a second backlink.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import Backlink, GuestPostOpportunity
from kb_contracts.enums import OpportunityStatus
from kb_contracts.events import Actor

from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.discovery.canonical import InvalidUrl, canonicalize_url
from kb_core.errors import EntityNotFound, InvalidInput
from kb_core.outreach.ports import ACTOR_NEGOTIATION, TransitionPort


def _url(value: str, what: str) -> str:
    try:
        return canonicalize_url(value).canonical
    except InvalidUrl as exc:
        raise InvalidInput(f"{what} is not a valid URL: {exc}") from exc


class OpportunityService:
    def __init__(self, uow_factory: UnitOfWorkFactory, transitions: TransitionPort) -> None:
        self._uow_factory = uow_factory
        self._tr = transitions

    @staticmethod
    async def _get(uow: UnitOfWork, opportunity_id: UUID) -> GuestPostOpportunity:
        opp = await uow.opportunities.get(opportunity_id)
        if opp is None:
            raise EntityNotFound("opportunity", opportunity_id)
        return opp

    async def _move(
        self,
        uow: UnitOfWork,
        opp: GuestPostOpportunity,
        target: OpportunityStatus,
        reason: str,
        correlation_id: str,
        actor: Actor,
    ) -> GuestPostOpportunity:
        moved = await self._tr.transition_in(
            uow, opp, target, actor=actor, reason=reason, correlation_id=correlation_id
        )
        result: GuestPostOpportunity = moved.entity
        return result

    async def start_content(
        self, opportunity_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_NEGOTIATION
    ) -> GuestPostOpportunity:
        async with self._uow_factory() as uow:
            opp = await self._get(uow, opportunity_id)
            return await self._move(
                uow,
                opp,
                OpportunityStatus.CONTENT_IN_PROGRESS,
                "content work started",
                correlation_id,
                actor,
            )

    async def submit_content(
        self,
        opportunity_id: UUID,
        *,
        title: str,
        content_ref: str,
        correlation_id: str,
        actor: Actor = ACTOR_NEGOTIATION,
    ) -> tuple[GuestPostOpportunity, dict[str, object]]:
        if not title.strip() or not content_ref.strip():
            raise InvalidInput("title and content_ref are required")
        async with self._uow_factory() as uow:
            opp = await self._get(uow, opportunity_id)
            moved = await self._move(
                uow, opp, OpportunityStatus.CONTENT_SUBMITTED, "content submitted",
                correlation_id, actor,
            )  # fmt: skip
            row = await uow.opportunities.record_submission(
                opportunity_id=opp.id, title=title, content_ref=content_ref
            )
            return moved, dict(row)

    async def request_revision(
        self, opportunity_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_NEGOTIATION
    ) -> GuestPostOpportunity:
        async with self._uow_factory() as uow:
            opp = await self._get(uow, opportunity_id)
            return await self._move(
                uow, opp, OpportunityStatus.REVISION_REQUESTED, "publisher requested changes",
                correlation_id, actor,
            )  # fmt: skip

    async def mark_lost(
        self, opportunity_id: UUID, *, reason: str, correlation_id: str,
        actor: Actor = ACTOR_NEGOTIATION,
    ) -> GuestPostOpportunity:  # fmt: skip
        async with self._uow_factory() as uow:
            opp = await self._get(uow, opportunity_id)
            return await self._move(uow, opp, OpportunityStatus.LOST, reason, correlation_id, actor)

    async def publish(
        self,
        opportunity_id: UUID,
        *,
        published_url: str,
        correlation_id: str,
        expected_anchor: str | None = None,
        actor: Actor = ACTOR_NEGOTIATION,
    ) -> tuple[GuestPostOpportunity, Backlink]:
        source = _url(published_url, "published_url")
        async with self._uow_factory() as uow:
            opp = await self._get(uow, opportunity_id)
            if not opp.target_url:
                raise InvalidInput("the opportunity has no target_url to link to")
            target = _url(opp.target_url, "target_url")
            moved = await self._move(
                uow, opp, OpportunityStatus.PUBLISHED, "content published", correlation_id, actor
            )
            await uow.opportunities.record_submission(
                opportunity_id=opp.id,
                title="published",
                content_ref=source,
                published_url=source,
            )
            backlink = await uow.backlinks.create(
                opportunity_id=opp.id,
                source_url=source,
                target_url=target,
                expected_anchor=expected_anchor,
            )
            return moved, backlink
