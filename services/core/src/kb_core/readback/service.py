"""Read-only use cases behind the outreach endpoints. Never mutates, never enqueues."""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import Backlink, GuestPostOpportunity, OutreachMessage, OutreachSequence
from kb_contracts.enums import BacklinkStatus, OpportunityStatus, SequenceStatus

from kb_core.db.protocols import Page, UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.readback.views import BacklinkDetailView, ThreadView


class OutreachReadService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def list_sequences(
        self,
        *,
        campaign_id: UUID | None = None,
        status: SequenceStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[OutreachSequence]:
        async with self._uow_factory() as uow:
            return await uow.sequences.list(
                campaign_id=campaign_id, status=status, cursor=cursor, limit=limit
            )

    async def get_sequence(self, sequence_id: UUID) -> OutreachSequence:
        async with self._uow_factory() as uow:
            found = await uow.sequences.get(sequence_id)
            if found is None:
                raise EntityNotFound("outreach_sequence", sequence_id)
            return found

    async def get_message(self, message_id: UUID) -> OutreachMessage:
        async with self._uow_factory() as uow:
            found = await uow.messages.get(message_id)
            if found is None:
                raise EntityNotFound("message", message_id)
            return found

    async def list_messages(self, sequence_id: UUID) -> list[OutreachMessage]:
        async with self._uow_factory() as uow:
            if await uow.sequences.get(sequence_id) is None:
                raise EntityNotFound("outreach_sequence", sequence_id)
            return list(await uow.messages.list_for_sequence(sequence_id))

    async def get_thread(self, thread_id: UUID) -> ThreadView:
        async with self._uow_factory() as uow:
            thread = await uow.threads.get(thread_id)
            if thread is None:
                raise EntityNotFound("thread", thread_id)
            seq_id: UUID = thread["sequence_id"]
            messages = await uow.messages.list_for_sequence(seq_id)
            replies = {r.id: r for r in await uow.threads.list_replies(thread_id)}
            replies.update({r.id: r for r in await uow.replies.list_for_sequence(seq_id)})
            return ThreadView(
                id=thread["id"],
                sequence_id=seq_id,
                subject=thread.get("subject"),
                provider_thread_id=thread.get("provider_thread_id"),
                messages=list(messages),
                replies=sorted(replies.values(), key=lambda r: (r.received_at, r.id)),
            )

    async def list_opportunities(
        self, *, status: OpportunityStatus | None = None, cursor: str | None = None, limit: int = 50
    ) -> Page[GuestPostOpportunity]:
        async with self._uow_factory() as uow:
            return await uow.opportunities.list(status=status, cursor=cursor, limit=limit)

    async def get_opportunity(self, opportunity_id: UUID) -> GuestPostOpportunity:
        async with self._uow_factory() as uow:
            found = await uow.opportunities.get(opportunity_id)
            if found is None:
                raise EntityNotFound("opportunity", opportunity_id)
            return found

    async def list_backlinks(
        self, *, status: BacklinkStatus | None = None, cursor: str | None = None, limit: int = 50
    ) -> Page[Backlink]:
        async with self._uow_factory() as uow:
            return await uow.backlinks.list(status=status, cursor=cursor, limit=limit)

    async def get_backlink(self, backlink_id: UUID) -> BacklinkDetailView:
        async with self._uow_factory() as uow:
            found = await uow.backlinks.get(backlink_id)
            if found is None:
                raise EntityNotFound("backlink", backlink_id)
            return BacklinkDetailView(
                backlink=found,
                verifications=list(await uow.backlinks.list_verifications(backlink_id)),
            )
