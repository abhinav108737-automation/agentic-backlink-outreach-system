"""Outreach job types, payload shapes and typed enqueue helpers (the Lane B / Lane E seam).

Lane B enqueues; Lane E implements the handlers; the Lead assembles the registry passed to
`JobWorker(handlers=...)`. `job.payload` is the validated dump of the model named below.
Payloads carry identifiers and small scalars only: never message bodies or secrets.

Retry, backoff and dead-lettering are inherited from `SqlJobRepository` and are not
reimplemented here: a handler exception moves the job to RETRY_WAIT with backoff until
`max_attempts` (default 5), then DEAD; an expired lease returns the job to SCHEDULED with
attempts + 1; a job type with no registered handler is dead-lettered.

Concurrency: `sequence_concurrency_key` serialises all jobs of one sequence, so two sends for
one sequence can never run at once (the repository claims one job per concurrency_key).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

OUTREACH_DRAFT = "outreach.draft"
OUTREACH_SEND = "outreach.send"
OUTREACH_FOLLOW_UP = "outreach.follow_up"
REPLIES_INGEST = "replies.ingest"
REPLIES_CLASSIFY = "replies.classify"
NEGOTIATION_PLAN = "negotiation.plan"
BACKLINK_VERIFY = "backlink.verify"

ALL_JOB_TYPES = (
    OUTREACH_DRAFT,
    OUTREACH_SEND,
    OUTREACH_FOLLOW_UP,
    REPLIES_INGEST,
    REPLIES_CLASSIFY,
    NEGOTIATION_PLAN,
    BACKLINK_VERIFY,
)


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    correlation_id: str


class DraftPayload(_Payload):
    sequence_id: UUID
    step_no: int


class SendPayload(_Payload):
    message_id: UUID
    sequence_id: UUID


class FollowUpPayload(_Payload):
    sequence_id: UUID
    step_no: int


class RepliesIngestPayload(_Payload):
    campaign_id: UUID
    sequence_id: UUID | None = None


class RepliesClassifyPayload(_Payload):
    reply_id: UUID
    sequence_id: UUID | None = None


class NegotiationPlanPayload(_Payload):
    reply_id: UUID
    sequence_id: UUID


class BacklinkVerifyPayload(_Payload):
    backlink_id: UUID


class _Queue(Protocol):
    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        run_at: datetime,
        idempotency_key: str,
        concurrency_key: str | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
    ) -> str: ...


def sequence_concurrency_key(sequence_id: UUID) -> str:
    return f"sequence:{sequence_id}"


async def _put(
    queue: _Queue,
    job_type: str,
    payload: _Payload,
    run_at: datetime,
    key: str,
    concurrency_key: str | None,
    subject: tuple[str, UUID] | None,
) -> str:
    """`subject` ties the job to an aggregate so a terminal transition can cancel it
    (`cancel_for_subject`) in the same transaction."""
    return await queue.enqueue(
        job_type,
        payload.model_dump(mode="json"),
        run_at,
        f"{job_type}:{key}",
        concurrency_key,
        subject_type=subject[0] if subject else None,
        subject_id=subject[1] if subject else None,
    )


SEQUENCE_SUBJECT = "sequence"


def _seq(sequence_id: UUID) -> tuple[str, UUID]:
    return (SEQUENCE_SUBJECT, sequence_id)


async def enqueue_draft(queue: _Queue, p: DraftPayload, run_at: datetime) -> str:
    return await _put(
        queue,
        OUTREACH_DRAFT,
        p,
        run_at,
        f"{p.sequence_id}:{p.step_no}",
        sequence_concurrency_key(p.sequence_id),
        _seq(p.sequence_id),
    )


async def enqueue_send(queue: _Queue, p: SendPayload, run_at: datetime) -> str:
    return await _put(
        queue,
        OUTREACH_SEND,
        p,
        run_at,
        str(p.message_id),
        sequence_concurrency_key(p.sequence_id),
        _seq(p.sequence_id),
    )


async def enqueue_follow_up(queue: _Queue, p: FollowUpPayload, run_at: datetime) -> str:
    return await _put(
        queue,
        OUTREACH_FOLLOW_UP,
        p,
        run_at,
        f"{p.sequence_id}:{p.step_no}",
        sequence_concurrency_key(p.sequence_id),
        _seq(p.sequence_id),
    )


async def enqueue_replies_ingest(queue: _Queue, p: RepliesIngestPayload, run_at: datetime) -> str:
    subject = _seq(p.sequence_id) if p.sequence_id else None
    return await _put(
        queue, REPLIES_INGEST, p, run_at, f"{p.campaign_id}:{p.correlation_id}", None, subject
    )


async def enqueue_replies_classify(
    queue: _Queue, p: RepliesClassifyPayload, run_at: datetime
) -> str:
    ck = sequence_concurrency_key(p.sequence_id) if p.sequence_id else None
    subject = _seq(p.sequence_id) if p.sequence_id else None
    return await _put(queue, REPLIES_CLASSIFY, p, run_at, str(p.reply_id), ck, subject)


async def enqueue_negotiation_plan(
    queue: _Queue, p: NegotiationPlanPayload, run_at: datetime
) -> str:
    return await _put(
        queue,
        NEGOTIATION_PLAN,
        p,
        run_at,
        str(p.reply_id),
        sequence_concurrency_key(p.sequence_id),
        _seq(p.sequence_id),
    )


async def enqueue_backlink_verify(queue: _Queue, p: BacklinkVerifyPayload, run_at: datetime) -> str:
    return await _put(
        queue,
        BACKLINK_VERIFY,
        p,
        run_at,
        f"{p.backlink_id}:{p.correlation_id}",
        f"backlink:{p.backlink_id}",
        ("backlink", p.backlink_id),
    )
