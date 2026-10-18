"""Approval endpoints (contract C8). Every decision is audited in the same transaction."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from kb_contracts.domain import ApprovalRequest
from kb_core.audit.service import OPERATOR

from kb_api.deps import ServicesDep, correlation_id
from kb_api.schemas.approvals import ApprovalPage, CreateApprovalRequest, DecideApprovalRequest

router = APIRouter(prefix="/approvals", tags=["Approvals"])


@router.get("", operation_id="listApprovals", response_model=ApprovalPage)
async def listApprovals(
    svc: ServicesDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApprovalPage:
    """Pending approvals, oldest first."""
    page = await svc.approvals.list_pending(cursor=cursor, limit=limit)
    return ApprovalPage(items=list(page.items), next_cursor=page.next_cursor)


@router.post("", operation_id="createApproval", status_code=201, response_model=ApprovalRequest)
async def createApproval(
    body: CreateApprovalRequest, request: Request, svc: ServicesDep
) -> ApprovalRequest:
    return await svc.approvals.create(
        kind=body.kind,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        requested_by=OPERATOR,
        reason=body.reason,
        correlation_id=correlation_id(request),
    )


@router.post("/{approval_id}/decide", operation_id="decideApproval", response_model=ApprovalRequest)
async def decideApproval(
    approval_id: UUID, body: DecideApprovalRequest, request: Request, svc: ServicesDep
) -> ApprovalRequest:
    return await svc.approvals.decide(
        approval_id,
        status=body.decision,
        decided_by=OPERATOR,
        reason=body.reason,
        correlation_id=correlation_id(request),
    )
