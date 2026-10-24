from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from kb_core.scheduling import jobs as j
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str, str | None]] = []
        self.subjects: list[tuple[str | None, UUID | None]] = []

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        run_at: datetime,
        idempotency_key: str,
        concurrency_key: str | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
    ) -> str:
        self.subjects.append((subject_type, subject_id))
        self.calls.append((job_type, payload, idempotency_key, concurrency_key))
        return idempotency_key


async def test_send_serialises_per_sequence_and_is_keyed_by_message() -> None:
    q, seq, msg = FakeQueue(), uuid4(), uuid4()
    await j.enqueue_send(q, j.SendPayload(message_id=msg, sequence_id=seq, correlation_id="c"), NOW)
    job_type, payload, key, ck = q.calls[0]
    assert job_type == j.OUTREACH_SEND == "outreach.send"
    assert key == f"outreach.send:{msg}"
    assert ck == f"sequence:{seq}"
    assert q.subjects[0] == ("sequence", seq)
    assert payload == {"correlation_id": "c", "message_id": str(msg), "sequence_id": str(seq)}


async def test_same_input_yields_same_idempotency_key() -> None:
    q, seq = FakeQueue(), uuid4()
    p = j.DraftPayload(sequence_id=seq, step_no=1, correlation_id="c")
    await j.enqueue_draft(q, p, NOW)
    await j.enqueue_draft(q, p, NOW)
    assert q.calls[0][2] == q.calls[1][2]


async def test_verify_uses_backlink_concurrency_key() -> None:
    q, b = FakeQueue(), uuid4()
    await j.enqueue_backlink_verify(
        q, j.BacklinkVerifyPayload(backlink_id=b, correlation_id="c"), NOW
    )
    assert q.calls[0][3] == f"backlink:{b}"


def test_payloads_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        j.BacklinkVerifyPayload(backlink_id=uuid4(), correlation_id="c", body="x")  # type: ignore[call-arg]


def test_job_type_list_is_the_agreed_seven() -> None:
    assert len(set(j.ALL_JOB_TYPES)) == 7
