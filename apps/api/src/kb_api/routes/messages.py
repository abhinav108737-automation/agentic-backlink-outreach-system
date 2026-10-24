"""Message endpoints (contract C8). Thin: parse, call the service, map. `send` only enqueues."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from kb_contracts.domain import OutreachMessage
from kb_core.audit.service import OPERATOR
from kb_core.scheduling.jobs import OUTREACH_SEND

from kb_api.deps import ServicesDep, correlation_id
from kb_api.schemas.outreach import ApproveMessageRequest, MessageList, SendAccepted

router = APIRouter(prefix="/messages", tags=["Outreach"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("", operation_id="listMessages", response_model=MessageList)
async def listMessages(sequence_id: UUID, svc: ServicesDep) -> MessageList:
    """A sequence's messages in step/version order."""
    return MessageList(items=await svc.readback.list_messages(sequence_id))


@router.get("/{message_id}", operation_id="getMessage", response_model=OutreachMessage)
async def getMessage(message_id: UUID, svc: ServicesDep) -> OutreachMessage:
    return await svc.readback.get_message(message_id)


@router.post("/{message_id}/approve", operation_id="approveMessage", response_model=OutreachMessage)
async def approveMessage(
    message_id: UUID,
    body: ApproveMessageRequest,
    request: Request,
    response: Response,
    svc: ServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> OutreachMessage:
    """Record the operator's decision and move the message (APPROVED or REJECTED) in one
    transaction. 409 when the message is not awaiting a decision."""
    message, replayed = await svc.send.approve(
        message_id,
        decision=body.decision,
        actor=OPERATOR,
        reason=body.reason,
        correlation_id=correlation_id(request),
        idempotency_key=idempotency_key,
    )
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return message


@router.post(
    "/{message_id}/send", operation_id="sendMessage", status_code=202, response_model=SendAccepted
)
async def sendMessage(
    message_id: UUID,
    request: Request,
    response: Response,
    svc: ServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> SendAccepted:
    """Re-check policy, mark the message QUEUED and enqueue `outreach.send`. Returns 202 with the
    job reference; it never sends. 409 with `block_codes` when policy refuses."""
    queued = await svc.send.enqueue_send(
        message_id,
        actor=OPERATOR,
        correlation_id=correlation_id(request),
        idempotency_key=idempotency_key,
    )
    if queued.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return SendAccepted(
        message=queued.message,
        job_id=queued.job_id,
        job_type=OUTREACH_SEND,
        replayed=queued.replayed,
    )
