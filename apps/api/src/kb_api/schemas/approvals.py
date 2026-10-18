from uuid import UUID

from kb_contracts.domain import ApprovalRequest
from kb_contracts.enums import ApprovalKind, ApprovalStatus
from pydantic import BaseModel, ConfigDict, Field


class CreateApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ApprovalKind
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class DecideApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalStatus  # APPROVED or REJECTED only; anything else is a 422
    reason: str | None = Field(default=None, max_length=500)


class ApprovalPage(BaseModel):
    items: list[ApprovalRequest]
    next_cursor: str | None = None
