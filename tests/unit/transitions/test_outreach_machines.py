"""Sequence and message machines through TransitionService: exhaustive invalid pairs, guards,
and the guards that must be atomic with the transition."""

import itertools

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    ApprovalKind,
    ApprovalStatus,
    JobStatus,
    MessageStatus,
    SequenceStatus,
    SuppressionScope,
)
from kb_contracts.state_machines import OUTREACH_MESSAGE, OUTREACH_SEQUENCE, InvalidTransition
from kb_core.audit.service import OPERATOR
from kb_core.errors import GuardFailed, InvalidInput
from kb_core.testing import fakes
from kb_core.testing.scenario import Scenario
from kb_core.transitions.service import SEQUENCE_TERMINAL

S, M = SequenceStatus, MessageStatus
SEQ_PAIRS = list(itertools.product(S, S))
MSG_PAIRS = list(itertools.product(M, M))


# COMPLETED_NO_REPLY is only reachable from FOLLOW_UP_DUE; the others from WAITING_REPLY.
_SRC = {t: (S.FOLLOW_UP_DUE if t is S.COMPLETED_NO_REPLY else S.WAITING_REPLY) for t in S}


async def _go(sc, entity, to, **kw):
    return await sc.transitions.transition(
        entity, to, actor=OPERATOR, reason="t", correlation_id="c", **kw
    )


async def _seq_at(sc, w, status):
    sc.store.sequences[w.sequence.id] = w.sequence.model_copy(update={"status": status})
    return sc.store.sequences[w.sequence.id]


@pytest.mark.parametrize(("src", "dst"), SEQ_PAIRS)
async def test_sequence_matrix(src, dst):
    sc = Scenario()
    w = await sc.world()
    seq = await _seq_at(sc, w, src)
    if OUTREACH_SEQUENCE.is_allowed(src, dst):
        assert (await _go(sc, seq, dst)).entity.status is dst
    else:
        with pytest.raises(InvalidTransition):
            await _go(sc, seq, dst)
        assert sc.store.sequences[seq.id].status is src
        assert sc.store.sequences[seq.id].version == seq.version  # untouched


@pytest.mark.parametrize(("src", "dst"), MSG_PAIRS)
async def test_message_matrix(src, dst):
    sc = Scenario(PolicyBody(require_approval_for_send=False))
    w = await sc.world(status=src)
    msg = sc.store.messages[w.message.id]
    if OUTREACH_MESSAGE.is_allowed(src, dst):
        try:
            res = await _go(sc, msg, dst, extras={"provider_message_id": "p-1"})
        except (GuardFailed, InvalidInput):
            return  # a legal edge may still be refused by its guard; never by the table
        assert res.entity.status is dst
    else:
        with pytest.raises(InvalidTransition):
            await _go(sc, msg, dst)
        assert sc.store.messages[msg.id].status is src


# -- message guards ---------------------------------------------------------------------------
async def test_qa_passed_needs_supported_claims():
    from uuid import uuid4

    sc = Scenario()
    w = await sc.world(status=M.DRAFT)
    bad = w.message.model_copy(update={"status": M.DRAFT, "claims": {"c": (uuid4(),)}})
    sc.store.messages[bad.id] = bad
    with pytest.raises(GuardFailed) as e:
        await _go(sc, bad, M.QA_PASSED)
    assert e.value.code == "UNSUPPORTED_CLAIM"


async def test_pending_to_approved_needs_an_approved_approval():
    sc = Scenario()
    w = await sc.world(status=M.PENDING_APPROVAL)
    msg = sc.store.messages[w.message.id]
    with pytest.raises(GuardFailed) as e:
        await _go(sc, msg, M.APPROVED)
    assert e.value.code == "APPROVAL_REQUIRED"
    async with sc.factory() as uow:
        a = await uow.approvals.create(
            kind=ApprovalKind.MESSAGE_SEND.value,
            subject_type="message",
            subject_id=msg.id,
            requested_by="operator",
        )
        await uow.approvals.decide(
            a.id, status=ApprovalStatus.APPROVED, decided_by="operator", reason=None
        )
    assert (await _go(sc, msg, M.APPROVED)).entity.status is M.APPROVED


async def test_pending_to_rejected_needs_a_rejected_approval():
    sc = Scenario()
    w = await sc.world(status=M.PENDING_APPROVAL)
    with pytest.raises(GuardFailed):
        await _go(sc, sc.store.messages[w.message.id], M.REJECTED)


async def test_qa_passed_skips_approval_only_when_policy_allows():
    strict, lax = Scenario(), Scenario(PolicyBody(require_approval_for_send=False))
    ws = await strict.world(status=M.QA_PASSED)
    with pytest.raises(GuardFailed):
        await _go(strict, strict.store.messages[ws.message.id], M.APPROVED)
    wl = await lax.world(status=M.QA_PASSED)
    assert (
        await _go(lax, lax.store.messages[wl.message.id], M.APPROVED)
    ).entity.status is M.APPROVED


async def test_sent_requires_the_provider_message_id():
    sc = Scenario()
    w = await sc.world(status=M.SENDING)
    msg = sc.store.messages[w.message.id]
    with pytest.raises(InvalidInput):
        await _go(sc, msg, M.SENT)
    res = await _go(sc, msg, M.SENT, extras={"provider_message_id": "p-9"})
    assert res.entity.status is M.SENT and sc.store.provider_ids[msg.id] == "p-9"


# -- terminal sequence: cancel work, suppress ------------------------------------------------
async def _with_work(sc, w):
    async with sc.factory() as uow:
        await uow.jobs.enqueue(
            job_type="outreach.send",
            payload={},
            run_at=sc.store.clock.now(),
            idempotency_key="j1",
            subject_type="sequence",
            subject_id=w.sequence.id,
        )
        await uow.jobs.enqueue(
            job_type="outreach.send",
            payload={},
            run_at=sc.store.clock.now(),
            idempotency_key="other",
            subject_type="sequence",
            subject_id=w.campaign.id,  # unrelated subject: must survive
        )


def _job_status(sc, key):
    return next(j["status"] for j in sc.store.jobs.values() if j["idempotency_key"] == key)


@pytest.mark.parametrize("terminal", sorted(SEQUENCE_TERMINAL, key=str))
async def test_terminal_sequence_cancels_pending_jobs_and_unsent_messages(terminal):
    sc = Scenario()
    w = await sc.world(status=M.QUEUED)
    sent = await sc.message(w.sequence, step_no=2, status=M.SENT, idempotency_key="sent")
    sending = await sc.message(w.sequence, step_no=3, status=M.SENDING, idempotency_key="sg")
    await _with_work(sc, w)
    seq = await _seq_at(sc, w, _SRC[terminal])
    await _go(sc, seq, terminal)
    assert _job_status(sc, "j1") is JobStatus.CANCELLED
    assert _job_status(sc, "other") is JobStatus.SCHEDULED
    assert sc.store.messages[w.message.id].status is M.CANCELLED
    assert sc.store.messages[sent.id].status is M.SENT  # already out: untouched
    assert sc.store.messages[sending.id].status is M.SENDING  # in flight: reconciled, not cancelled


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (S.OPTED_OUT, True),
        (S.BOUNCED, True),
        (S.REPLIED, False),
        (S.CANCELLED, False),
        (S.COMPLETED_NO_REPLY, False),
    ],
)
async def test_only_opt_out_and_bounce_create_suppression(terminal, expected):
    sc = Scenario()
    w = await sc.world()
    seq = await _seq_at(sc, w, _SRC[terminal])
    await _go(sc, seq, terminal)
    entries = sc.store.suppressions
    assert bool(entries) is expected
    if expected:
        assert entries[0].scope is SuppressionScope.EMAIL
        assert entries[0].value_normalized == "editor@example.com"
        assert entries[0].campaign_id is None
        assert entries[0].reason.value == ("opt_out" if terminal is S.OPTED_OUT else "bounce")


@pytest.mark.parametrize("terminal", [S.OPTED_OUT, S.BOUNCED])
async def test_failure_after_suppression_rolls_back_everything(terminal, monkeypatch):
    """The final audit row is written AFTER jobs are cancelled, messages cancelled and the
    suppression entry created; failing it must undo all of them and the status change."""
    sc = Scenario()
    w = await sc.world(status=M.QUEUED)
    await _with_work(sc, w)
    seq = await _seq_at(sc, w, S.WAITING_REPLY)
    before_audit = len(sc.store.audit)
    real = fakes.FakeAudit.append

    async def flaky(self, **kw):
        if kw["action"] == "outreach_sequence.transition":
            raise RuntimeError("audit store unavailable")
        return await real(self, **kw)

    monkeypatch.setattr(fakes.FakeAudit, "append", flaky)
    with pytest.raises(RuntimeError):
        await _go(sc, seq, terminal)
    assert sc.store.sequences[seq.id].status is S.WAITING_REPLY
    assert sc.store.sequences[seq.id].version == seq.version
    assert _job_status(sc, "j1") is JobStatus.SCHEDULED
    assert sc.store.messages[w.message.id].status is M.QUEUED
    assert sc.store.suppressions == []
    assert len(sc.store.audit) == before_audit


async def test_missing_channel_aborts_an_opt_out_atomically():
    sc = Scenario()
    w = await sc.world(status=M.QUEUED)
    sc.store.channels.clear()
    seq = await _seq_at(sc, w, S.WAITING_REPLY)
    with pytest.raises(GuardFailed) as e:
        await _go(sc, seq, S.OPTED_OUT)
    assert e.value.code == "CONTACT_CHANNEL_MISSING"
    assert sc.store.sequences[seq.id].status is S.WAITING_REPLY
    assert sc.store.messages[w.message.id].status is M.QUEUED
