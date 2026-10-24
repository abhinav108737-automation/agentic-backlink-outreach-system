"""Backlink endpoints (contract C8). `verify` enqueues; verification runs in the worker."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from kb_contracts.enums import BacklinkStatus
from kb_core.audit.service import OPERATOR
from kb_core.readback.views import BacklinkDetailView
from kb_core.scheduling.jobs import BACKLINK_VERIFY

from kb_api.deps import ServicesDep, correlation_id
from kb_api.schemas.outreach import BacklinkPage, VerifyAccepted

router = APIRouter(prefix="/backlinks", tags=["Backlinks"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("", operation_id="listBacklinks", response_model=BacklinkPage)
async def listBacklinks(
    svc: ServicesDep,
    status: BacklinkStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BacklinkPage:
    page = await svc.readback.list_backlinks(status=status, cursor=cursor, limit=limit)
    return BacklinkPage(items=list(page.items), next_cursor=page.next_cursor)


@router.get("/{backlink_id}", operation_id="getBacklink", response_model=BacklinkDetailView)
async def getBacklink(backlink_id: UUID, svc: ServicesDep) -> BacklinkDetailView:
    """The backlink and its append-only verification history."""
    return await svc.readback.get_backlink(backlink_id)


@router.post(
    "/{backlink_id}/verify",
    operation_id="verifyBacklink",
    status_code=202,
    response_model=VerifyAccepted,
)
async def verifyBacklink(
    backlink_id: UUID,
    request: Request,
    response: Response,
    svc: ServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> VerifyAccepted:
    """Enqueue `backlink.verify`; returns 202 with the job reference and fetches nothing."""
    queued = await svc.backlink_verify.request(
        backlink_id,
        actor=OPERATOR,
        correlation_id=correlation_id(request),
        idempotency_key=idempotency_key,
    )
    if queued.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return VerifyAccepted(
        backlink_id=backlink_id,
        job_id=queued.job_id,
        job_type=BACKLINK_VERIFY,
        replayed=queued.replayed,
    )
