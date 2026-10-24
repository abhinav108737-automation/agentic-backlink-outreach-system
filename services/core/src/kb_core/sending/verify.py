"""Backlink verification request: enqueue `backlink.verify`, never execute it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from kb_contracts.events import Actor
from kb_contracts.providers import Clock

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.idempotency.service import IdempotencyService
from kb_core.scheduling.jobs import BacklinkVerifyPayload, enqueue_backlink_verify
from kb_core.scheduling.queue import InUowQueue

SCOPE_VERIFY = "backlink.verify"


@dataclass(frozen=True)
class EnqueuedVerify:
    backlink_id: UUID
    job_id: UUID
    replayed: bool


class BacklinkVerifyService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._idem = idempotency or IdempotencyService()

    async def request(
        self,
        backlink_id: UUID,
        *,
        actor: Actor,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> EnqueuedVerify:
        async with self._uow_factory() as uow:
            if await uow.backlinks.get(backlink_id) is None:
                raise EntityNotFound("backlink", backlink_id)

            async def op() -> str:
                job_id = await enqueue_backlink_verify(
                    InUowQueue(uow),
                    BacklinkVerifyPayload(backlink_id=backlink_id, correlation_id=correlation_id),
                    self._clock.now(),
                )
                await AuditTrail(uow.audit).record(
                    actor=actor,
                    action="backlink.verify_requested",
                    subject_type="backlink",
                    subject_id=backlink_id,
                    correlation_id=correlation_id,
                    new_state={"job_id": job_id},
                )
                return f"job:{job_id}"

            if idempotency_key is None:
                return EnqueuedVerify(backlink_id, UUID((await op()).split(":")[1]), False)
            outcome = await self._idem.execute(
                uow,
                scope=SCOPE_VERIFY,
                key=idempotency_key,
                payload={"backlink_id": str(backlink_id)},
                operation=op,
            )
            return EnqueuedVerify(backlink_id, UUID(outcome.ref.split(":")[1]), outcome.replayed)
