"""Request/response bodies for the I2 outreach endpoints. Responses reuse frozen C2 DTOs."""

from uuid import UUID

from kb_contracts.domain import Backlink, GuestPostOpportunity, OutreachMessage, OutreachSequence
from kb_contracts.enums import ApprovalStatus
from pydantic import BaseModel, ConfigDict, Field


class ApproveMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalStatus  # APPROVED or REJECTED only; anything else is a 422
    reason: str | None = Field(default=None, max_length=500)


class SendAccepted(BaseModel):
    """202: the send was queued, not performed. Delivery is the worker's job."""

    message: OutreachMessage
    job_id: UUID
    job_type: str
    replayed: bool = False


class VerifyAccepted(BaseModel):
    backlink_id: UUID
    job_id: UUID
    job_type: str
    replayed: bool = False


class SequencePage(BaseModel):
    items: list[OutreachSequence]
    next_cursor: str | None = None


class MessageList(BaseModel):
    items: list[OutreachMessage]


class OpportunityPage(BaseModel):
    items: list[GuestPostOpportunity]
    next_cursor: str | None = None


class BacklinkPage(BaseModel):
    items: list[Backlink]
    next_cursor: str | None = None
