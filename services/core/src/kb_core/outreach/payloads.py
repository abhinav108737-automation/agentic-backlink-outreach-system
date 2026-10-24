"""Job payload models for the seven Lane E job types.

These mirror the models Lane B enqueues (`kb_core.scheduling.jobs`), which Lane E must not import.
`extra="ignore"` keeps a handler working if Lane B adds an optional field; a missing or malformed
required field raises `InvalidInput`, so the worker retries and finally dead-letters the job for
human review rather than guessing.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import ScheduledJob
from pydantic import BaseModel, ConfigDict, ValidationError

from kb_core.errors import InvalidInput

OUTREACH_DRAFT = "outreach.draft"
OUTREACH_SEND = "outreach.send"
OUTREACH_FOLLOW_UP = "outreach.follow_up"
REPLIES_INGEST = "replies.ingest"
REPLIES_CLASSIFY = "replies.classify"
NEGOTIATION_PLAN = "negotiation.plan"
BACKLINK_VERIFY = "backlink.verify"


class _P(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    correlation_id: str


class DraftJob(_P):
    sequence_id: UUID
    step_no: int


class SendJob(_P):
    message_id: UUID
    sequence_id: UUID


class FollowUpJob(_P):
    sequence_id: UUID
    step_no: int


class RepliesIngestJob(_P):
    campaign_id: UUID
    sequence_id: UUID | None = None


class RepliesClassifyJob(_P):
    reply_id: UUID
    sequence_id: UUID | None = None


class NegotiationPlanJob(_P):
    reply_id: UUID
    sequence_id: UUID


class BacklinkVerifyJob(_P):
    backlink_id: UUID


def parse_payload[P: _P](model: type[P], job: ScheduledJob) -> P:
    try:
        return model.model_validate(job.payload)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(x) for x in e["loc"]) for e in exc.errors())
        raise InvalidInput(f"{job.job_type} payload invalid ({fields})") from exc


def sequence_concurrency_key(sequence_id: UUID) -> str:
    """Same key Lane B uses, so jobs this lane chains serialise with that lane's."""
    return f"sequence:{sequence_id}"
