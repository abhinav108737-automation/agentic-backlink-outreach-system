"""Read models for the outreach readback endpoints. They compose frozen C2 DTOs; no new
contract type is introduced."""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import Backlink, BacklinkVerification, OutreachMessage, Reply
from pydantic import BaseModel


class ThreadView(BaseModel):
    id: UUID
    sequence_id: UUID
    subject: str | None = None
    provider_thread_id: str | None = None
    messages: list[OutreachMessage]
    replies: list[Reply]


class BacklinkDetailView(BaseModel):
    backlink: Backlink
    verifications: list[BacklinkVerification]
