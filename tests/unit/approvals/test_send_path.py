"""approve -> enqueue -> begin_send -> complete/fail, over fakes (no DB)."""

from uuid import UUID

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    ApprovalStatus,
    JobStatus,
    MessageStatus,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.policy import BlockCode
from kb_contracts.state_machines import InvalidTransition
from kb_core.approvals.service import ApprovalService
from kb_core.audit.service import OPERATOR
from kb_core.errors import IdempotencyKeyReused, InvalidInput, PolicyBlocked
from kb_core.scheduling.jobs import OUTREACH_SEND
from kb_core.sending.service import MAX_SEND_ATTEMPTS, BeginOutcome, SendService
from kb_core.settings.service import SettingsService
from kb_core.suppression.service import SuppressionService
from kb_core.testing.scenario import Scenario

M = MessageStatus


def mk(sc):
    return SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    )


def lax():
    return Scenario(PolicyBody(require_approval_for_send=False))


async def kill(sc):
    await SettingsService(sc.factory).set_kill_switch(
        active=True, reason="stop", actor=OPERATOR, correlation_id="k"
    )


async def suppress(sc, value="editor@example.com"):
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=value,
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )


def jobs(sc):
    return list(sc.store.jobs.values())


# -- approve ----------------------------------------------------------------------------------
async def test_approve_goes_through_pending_approval_and_records_the_decision():
    sc = Scenario()
    w = await sc.world(status=M.QA_PASSED)
    msg, replayed = await mk(sc).approve(
        w.message.id, decision=ApprovalStatus.APPROVED, actor=OPERATOR, reason="ok",
        correlation_id="c",
    )  # fmt: skip
    assert msg.status is M.APPROVED and not replayed
    (a,) = sc.store.approvals.values()
    assert a.status is ApprovalStatus.APPROVED and a.subject_id == w.message.id
    actions = [r["action"] for r in sc.store.audit]
    assert actions.count("outreach_message.transition") == 2  # QA_PASSED->PENDING->APPROVED
    assert "approval.approved" in actions


async def test_reject_decision_rejects_the_message():
    sc = Scenario()
    w = await sc.world(status=M.PENDING_APPROVAL)
    msg, _ = await mk(sc).approve(
        w.message.id, decision=ApprovalStatus.REJECTED, actor=OPERATOR, reason="no",
        correlation_id="c",
    )  # fmt: skip
    assert msg.status is M.REJECTED


async def test_approve_skips_the_queue_when_policy_does_not_require_it():
    sc = lax()
    w = await sc.world(status=M.QA_PASSED)
    msg, _ = await mk(sc).approve(
        w.message.id, decision=ApprovalStatus.APPROVED, actor=OPERATOR, reason=None,
        correlation_id="c",
    )  # fmt: skip
    assert msg.status is M.APPROVED and not sc.store.approvals


@pytest.mark.parametrize("status", [M.DRAFT, M.APPROVED, M.QUEUED, M.SENT, M.CANCELLED])
async def test_approve_from_a_non_decidable_state_is_an_invalid_transition(status):
    sc = Scenario()
    w = await sc.world(status=status)
    with pytest.raises(InvalidTransition):
        await mk(sc).approve(
            w.message.id, decision=ApprovalStatus.APPROVED, actor=OPERATOR, reason=None,
            correlation_id="c",
        )  # fmt: skip
    assert sc.store.messages[w.message.id].status is status


async def test_approve_rejects_a_non_decision():
    sc = Scenario()
    w = await sc.world(status=M.QA_PASSED)
    with pytest.raises(InvalidInput):
        await mk(sc).approve(
            w.message.id, decision=ApprovalStatus.PENDING, actor=OPERATOR, reason=None,
            correlation_id="c",
        )  # fmt: skip


async def test_approve_replay_with_the_same_key_makes_no_second_approval():
    sc = Scenario()
    w = await sc.world(status=M.QA_PASSED)
    kw = dict(decision=ApprovalStatus.APPROVED, actor=OPERATOR, reason=None, correlation_id="c")
    first, r1 = await mk(sc).approve(w.message.id, idempotency_key="k1", **kw)
    again, r2 = await mk(sc).approve(w.message.id, idempotency_key="k1", **kw)
    assert (r1, r2) == (False, True) and first.id == again.id
    assert len(sc.store.approvals) == 1
    with pytest.raises(IdempotencyKeyReused):
        await mk(sc).approve(
            w.message.id, idempotency_key="k1", decision=ApprovalStatus.REJECTED,
            actor=OPERATOR, reason=None, correlation_id="c",
        )  # fmt: skip


# -- enqueue_send: enqueues, never sends -------------------------------------------------------
async def test_enqueue_send_queues_the_message_and_one_job_without_sending():
    sc = lax()
    w = await sc.world()
    out = await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert out.message.status is M.QUEUED  # not SENDING, not SENT
    (job,) = jobs(sc)
    assert job["job_type"] == OUTREACH_SEND and job["status"] is JobStatus.SCHEDULED
    assert job["id"] == out.job_id
    assert job["concurrency_key"] == f"sequence:{w.sequence.id}"
    assert job["subject_type"] == "sequence" and job["subject_id"] == w.sequence.id
    assert set(job["payload"]) == {"message_id", "sequence_id", "correlation_id"}  # no body
    assert sc.store.provider_ids == {}


async def test_enqueue_send_replay_returns_the_same_job():
    sc = lax()
    w = await sc.world()
    a = await mk(sc).enqueue_send(
        w.message.id, actor=OPERATOR, correlation_id="c1", idempotency_key="k"
    )
    b = await mk(sc).enqueue_send(
        w.message.id, actor=OPERATOR, correlation_id="c2", idempotency_key="k"
    )
    assert a.job_id == b.job_id and not a.replayed and b.replayed
    assert len(jobs(sc)) == 1


async def test_enqueue_send_twice_without_a_key_still_has_one_job():
    sc = lax()
    w = await sc.world()
    a = await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    b = await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert a.job_id == b.job_id and len(jobs(sc)) == 1


async def test_enqueue_send_requires_an_approved_message():
    sc = lax()
    w = await sc.world(status=M.QA_PASSED)
    with pytest.raises(InvalidTransition):
        await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert jobs(sc) == []


async def test_kill_switch_blocks_enqueue_and_leaves_the_message_retryable():
    sc = lax()
    w = await sc.world()
    await kill(sc)
    with pytest.raises(PolicyBlocked) as e:
        await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert BlockCode.KILL_SWITCH_ACTIVE in e.value.decision.block_codes
    assert sc.store.messages[w.message.id].status is M.APPROVED and jobs(sc) == []
    assert any(r["action"] == "policy.blocked" for r in sc.store.audit)  # block was committed


async def test_permanent_block_at_enqueue_moves_the_message_to_policy_blocked():
    sc = lax()
    w = await sc.world()
    await suppress(sc)
    with pytest.raises(PolicyBlocked):
        await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert sc.store.messages[w.message.id].status is M.POLICY_BLOCKED and jobs(sc) == []


async def test_real_delivery_cannot_be_queued_while_real_send_is_off():
    sc = lax()
    w = await sc.world(delivery_mode="real")
    with pytest.raises(PolicyBlocked) as e:
        await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert BlockCode.REAL_SEND_DISABLED in e.value.decision.block_codes
    assert jobs(sc) == []


# -- begin_send: the pre-send re-check ---------------------------------------------------------
async def queued(sc, **kw):
    w = await sc.world(status=M.QUEUED, **kw)
    return w


async def test_begin_send_moves_to_sending_once_then_asks_for_reconciliation():
    sc = lax()
    w = await queued(sc)
    first = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert first.outcome is BeginOutcome.PROCEED and first.attempt == 1
    assert first.message.status is M.SENDING
    # a crash after the claim: the retry finds SENDING and must reconcile, never resend
    second = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert second.outcome is BeginOutcome.RECONCILE
    assert sc.store.attempts[w.message.id] == 1  # no second claim


async def test_suppression_added_after_approval_stops_the_send_before_sending():
    sc = lax()
    w = await queued(sc)
    await suppress(sc)
    res = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.BLOCKED
    assert BlockCode.SUPPRESSED in res.decision.block_codes
    assert sc.store.messages[w.message.id].status is M.POLICY_BLOCKED


async def test_kill_switch_defers_a_queued_send_without_losing_it():
    sc = lax()
    w = await queued(sc)
    await kill(sc)
    res = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.KILL_SWITCH_ACTIVE in res.decision.block_codes
    assert sc.store.messages[w.message.id].status is M.QUEUED


async def test_real_delivery_is_deferred_at_the_pre_send_check_too():
    sc = lax()
    w = await queued(sc, delivery_mode="real")
    res = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.REAL_SEND_DISABLED in res.decision.block_codes


@pytest.mark.parametrize("status", [M.SENT, M.DELIVERED, M.BOUNCED])
async def test_begin_send_on_an_already_sent_message_is_a_no_op(status):
    sc = lax()
    w = await sc.world(status=status)
    res = await mk(sc).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.ALREADY_SENT


@pytest.mark.parametrize("status", [M.CANCELLED, M.APPROVED, M.DRAFT, M.POLICY_BLOCKED])
async def test_begin_send_ignores_messages_that_are_not_queued(status):
    sc = lax()
    w = await sc.world(status=status)
    assert (await mk(sc).begin_send(w.message.id, correlation_id="c")).outcome is (
        BeginOutcome.NOT_SENDABLE
    )


async def test_begin_send_ignores_a_terminal_sequence():
    sc = lax()
    w = await queued(sc)
    sc.store.sequences[w.sequence.id] = w.sequence.model_copy(
        update={"status": SequenceStatus.OPTED_OUT}
    )
    assert (await mk(sc).begin_send(w.message.id, correlation_id="c")).outcome is (
        BeginOutcome.NOT_SENDABLE
    )


# -- complete / fail ----------------------------------------------------------------------------
async def sending(sc, seq_status=SequenceStatus.READY):
    w = await queued(sc)
    sc.store.sequences[w.sequence.id] = w.sequence.model_copy(update={"status": seq_status})
    await mk(sc).begin_send(w.message.id, correlation_id="c")
    return w


async def test_complete_send_records_the_provider_id_and_advances_the_sequence():
    sc = lax()
    w = await sending(sc)
    msg = await mk(sc).complete_send(w.message.id, provider_message_id="p-1", correlation_id="c")
    assert msg.status is M.SENT and sc.store.provider_ids[msg.id] == "p-1"
    seq = sc.store.sequences[w.sequence.id]
    assert seq.status is SequenceStatus.WAITING_REPLY and seq.current_step == 1


async def test_complete_send_is_idempotent():
    sc = lax()
    w = await sending(sc)
    await mk(sc).complete_send(w.message.id, provider_message_id="p-1", correlation_id="c")
    again = await mk(sc).complete_send(w.message.id, provider_message_id="p-1", correlation_id="c")
    assert again.status is M.SENT and sc.store.sequences[w.sequence.id].current_step == 1


async def test_fail_send_retries_then_requeues_then_dead_ends_after_max_attempts():
    sc = lax()
    w = await sending(sc)
    m = await mk(sc).fail_send(
        w.message.id, attempt=1, retryable=True, error="timeout", correlation_id="c"
    )
    assert m.status is M.RETRY_WAIT
    again = await mk(sc).begin_send(
        w.message.id, correlation_id="c"
    )  # RETRY_WAIT -> QUEUED -> SENDING
    assert again.outcome is BeginOutcome.PROCEED and again.attempt == 2
    final = await mk(sc).fail_send(
        w.message.id, attempt=MAX_SEND_ATTEMPTS, retryable=True, error="x", correlation_id="c"
    )
    assert final.status is M.FAILED


async def test_non_retryable_failure_is_failed_immediately():
    sc = lax()
    w = await sending(sc)
    m = await mk(sc).fail_send(
        w.message.id, attempt=1, retryable=False, error="550 rejected", correlation_id="c"
    )
    assert m.status is M.FAILED


async def test_job_ids_are_uuids():
    sc = lax()
    w = await sc.world()
    out = await mk(sc).enqueue_send(w.message.id, actor=OPERATOR, correlation_id="c")
    assert isinstance(out.job_id, UUID)
