"""CCR-004 over real PostgreSQL: per-step delays from the previous send, the stop conditions,
the sending window at dispatch, and a suppression landing after enqueue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from flowkit import CORR, World, approved_and_queued, job, send_handler, send_job
from kb_contracts.agents import DraftMessage
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    ApprovalStatus,
    CampaignStatus,
    MessageStatus,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_core.audit.service import OPERATOR
from kb_core.db.seed import canonical_json, policy_content_hash
from kb_core.db.uow import SqlUnitOfWork
from kb_core.outreach.handlers import make_draft_handler, make_follow_up_handler
from kb_core.settings.service import SettingsService
from kb_core.suppression.service import SuppressionService

M = MessageStatus
S = SequenceStatus

# Generous limits so only the behaviour under test can block (real row timestamps are wall-clock).
LOOSE: dict[str, Any] = {
    "daily_send_limit": 99,
    "per_domain_send_limit": 99,
    "per_contact_send_limit": 99,
}


async def pin_policy(world: World, **overrides: Any) -> PolicyBody:
    """Insert a new campaign-scoped policy version and pin it to the world's campaign."""
    body = PolicyBody(**{**LOOSE, **overrides})
    assert canonical_json(body)
    async with world.factory() as uow:
        assert isinstance(uow, SqlUnitOfWork)
        s = uow.session
        pid = (
            await s.execute(
                sa.text(
                    "INSERT INTO policies (key, name, scope, risk_class, created_at, updated_at)"
                    " VALUES (:k, 'test', 'campaign', 'L3', now(), now()) RETURNING id"
                ),
                {"k": f"test.{uuid4()}"},
            )
        ).scalar_one()
        vid = (
            await s.execute(
                sa.text(
                    "INSERT INTO policy_versions (policy_id, version_no, body, content_hash,"
                    " status, created_by, approved_by, approved_at, created_at, updated_at)"
                    " VALUES (:p, 1, CAST(:b AS jsonb), :h, 'ACTIVE', 't', 't', now(), now(),"
                    " now()) RETURNING id"
                ),
                {"p": pid, "b": body.model_dump_json(), "h": policy_content_hash(body)},
            )
        ).scalar_one()
        await s.execute(
            sa.text("UPDATE campaigns SET policy_version_id = :v WHERE id = :c"),
            {"v": vid, "c": world.campaign.id},
        )
    return body


async def jobs(world: World, job_type: str) -> list[Any]:
    return await world.sql(
        "select status, payload, run_at from scheduled_jobs where job_type = :t"
        " order by created_at",
        t=job_type,
    )


async def live_jobs(world: World, job_type: str) -> list[Any]:
    return [j for j in await jobs(world, job_type) if j["status"] not in ("CANCELLED",)]


_TOPICS = {
    1: "Your readers covering market research might enjoy our regional logistics findings.",
    2: "Following up with a different angle: pharmaceutical packaging demand shifts in 2026.",
    3: "One last note from our analysts about semiconductor equipment lead times worldwide.",
}


async def _distinct_draft(world: World, step_no: int) -> None:
    async def produce(ctx: Any) -> DraftMessage:
        return DraftMessage(
            sequence_id=ctx.sequence.id,
            step_no=ctx.step_no,
            subject=f"Guest article idea {step_no}",
            body=_TOPICS[step_no],
            claims=(),
        )

    handler = make_draft_handler(world.factory, world.transitions, produce)
    await handler(
        job("outreach.draft", {"sequence_id": str(world.sequence.id), "step_no": step_no})
    )


async def send_step(world: World, step_no: int) -> datetime:
    """draft -> approve -> queue -> deliver step `step_no`; returns the clock at delivery."""
    await _distinct_draft(world, step_no)
    m = [x for x in await _msgs(world) if x.step_no == step_no][-1]
    await world.gate.approve(
        m.id, decision=ApprovalStatus.APPROVED, actor=OPERATOR, reason="ok", correlation_id=CORR
    )
    await world.gate.enqueue_send(m.id, actor=OPERATOR, correlation_id=CORR)
    _, handler = send_handler(world)
    await handler(send_job(m))
    return world.clock.now()


def follow_up(world: World):
    return make_follow_up_handler(world.factory, world.transitions, world.policy, world.clock)


def fu_job(world: World, step_no: int):
    return job("outreach.follow_up", {"sequence_id": str(world.sequence.id), "step_no": step_no})


async def seq_status(world: World) -> SequenceStatus:
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None
        return seq.status


# ====================================================================== delays
async def test_delay_zero_is_measured_from_the_send_not_from_scheduling(world: World) -> None:
    await pin_policy(world)
    m = await approved_and_queued(world)  # scheduled / queued at T0
    world.clock.advance(timedelta(hours=3))  # the worker only gets to it 3h later
    _, handler = send_handler(world)
    await handler(send_job(m))
    sent_at = world.clock.now()
    (fu,) = await jobs(world, "outreach.follow_up")
    assert fu["payload"]["step_no"] == 2
    assert fu["run_at"] == sent_at + timedelta(days=4)  # delays[0], not T0 + 4d
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.next_due_at == sent_at + timedelta(days=4)


async def test_delay_one_is_measured_from_the_first_follow_up_send(world: World) -> None:
    await pin_policy(world)
    await send_step(world, 1)
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    world.clock.advance(timedelta(hours=5))  # approval / worker latency
    sent_at = await send_step(world, 2)
    steps = {j["payload"]["step_no"]: j for j in await jobs(world, "outreach.follow_up")}
    assert steps[3]["run_at"] == sent_at + timedelta(days=7)  # delays[1]


async def test_a_follow_up_beyond_max_follow_ups_completes_instead_of_sending(
    world: World,
) -> None:
    await pin_policy(world)  # max_follow_ups == 2: steps 1..3
    await send_step(world, 1)
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await send_step(world, 2)
    world.clock.advance(timedelta(days=7))
    await follow_up(world)(fu_job(world, 3))
    sent_at = await send_step(world, 3)
    steps = {j["payload"]["step_no"]: j for j in await jobs(world, "outreach.follow_up")}
    assert steps[4]["run_at"] == sent_at + timedelta(days=7)  # completion timer, not a follow-up
    drafts_before = len(await jobs(world, "outreach.draft"))
    world.clock.advance(timedelta(days=7))
    await follow_up(world)(fu_job(world, 4))
    assert await seq_status(world) is S.COMPLETED_NO_REPLY
    assert len(await jobs(world, "outreach.draft")) == drafts_before  # nothing new to send


async def test_a_stricter_campaign_policy_wins(world: World) -> None:
    await pin_policy(world, max_follow_ups=1)
    await send_step(world, 1)
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await send_step(world, 2)
    steps = {j["payload"]["step_no"]: j for j in await jobs(world, "outreach.follow_up")}
    assert 3 in steps  # a completion timer
    world.clock.advance(timedelta(days=7))
    await follow_up(world)(fu_job(world, 3))
    assert await seq_status(world) is S.COMPLETED_NO_REPLY


async def test_delays_are_wall_clock_days_in_the_policy_timezone_across_dst(
    world: World,
) -> None:
    await pin_policy(
        world,
        timezone="America/New_York",
        sending_window_start_hour=9,
        sending_window_end_hour=15,
        follow_up_delays_days=(1, 7),
    )
    # Sat 7 Mar 2026 19:30 UTC = 14:30 EST (in window); DST starts Sun 8 Mar
    world.clock.advance(datetime(2026, 3, 7, 19, 30, tzinfo=UTC) - world.clock.now())
    await send_step(world, 1)
    (fu,) = await jobs(world, "outreach.follow_up")
    assert fu["run_at"] == datetime(2026, 3, 8, 18, 30, tzinfo=UTC)  # 14:30 EDT, not 19:30 UTC


# ====================================================================== stop conditions
async def _waiting(world: World) -> None:
    await pin_policy(world)
    await send_step(world, 1)
    assert (await live_jobs(world, "outreach.follow_up"))[0]["status"] == "SCHEDULED"


async def _assert_stopped(world: World, expected: SequenceStatus) -> None:
    assert await seq_status(world) is expected
    assert await live_jobs(world, "outreach.follow_up") == []  # the pending timer is gone
    assert await jobs(world, "outreach.draft") == []  # and nothing was drafted


async def _assert_held(world: World) -> None:
    """A reversible control (campaign pause, kill switch) must HOLD, not cancel.

    `CANCELLED` is terminal and campaign status is `ACTIVE <-> PAUSED` (docs/13), so cancelling
    here would destroy every in-flight sequence the moment an operator paused a campaign and
    resuming would bring none of them back. The sequence therefore keeps its waiting state, the
    timer is re-armed, and nothing is drafted.
    """
    status = await seq_status(world)
    assert status in (S.WAITING_REPLY, S.FOLLOW_UP_DUE), status
    assert status is not S.CANCELLED
    assert await live_jobs(world, "outreach.follow_up") != [], "the timer must be re-armed"
    assert await jobs(world, "outreach.draft") == []  # and nothing was drafted


async def _transition(world: World, to: SequenceStatus) -> None:
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None
        await world.transitions.transition_in(
            uow, seq, to, actor=OPERATOR, reason="test", correlation_id=CORR
        )


async def test_stop_on_reply(world: World) -> None:
    await _waiting(world)
    await _transition(world, S.REPLIED)
    await follow_up(world)(fu_job(world, 2))  # even a surviving job is a no-op
    await _assert_stopped(world, S.REPLIED)


async def test_stop_on_opt_out(world: World) -> None:
    await _waiting(world)
    await _transition(world, S.OPTED_OUT)
    await follow_up(world)(fu_job(world, 2))
    await _assert_stopped(world, S.OPTED_OUT)


async def test_stop_on_bounce(world: World) -> None:
    await _waiting(world)
    await _transition(world, S.BOUNCED)
    await follow_up(world)(fu_job(world, 2))
    await _assert_stopped(world, S.BOUNCED)


async def test_stop_on_suppression_match(world: World) -> None:
    await _waiting(world)
    async with world.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=world.channel_email,
            reason=SuppressionReason.MANUAL,
            actor=OPERATOR,
            correlation_id=CORR,
        )
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await _assert_stopped(world, S.CANCELLED)


async def test_stop_on_campaign_pause(world: World) -> None:
    await _waiting(world)
    async with world.factory() as uow:
        c = await uow.campaigns.get(world.campaign.id)
        assert c is not None
        await uow.campaigns.set_status(
            c.id, CampaignStatus.PAUSED.value, expected_version=c.version
        )
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await _assert_held(world)


async def test_stop_on_kill_switch(world: World) -> None:
    await _waiting(world)
    await SettingsService(world.factory).set_kill_switch(
        active=True, reason="stop", actor=OPERATOR, correlation_id=CORR
    )
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await _assert_held(world)


async def test_a_held_sequence_resumes_after_the_campaign_is_reactivated(world: World) -> None:
    """The point of holding rather than cancelling: the sequence survives the pause."""
    await _waiting(world)
    async with world.factory() as uow:
        c = await uow.campaigns.get(world.campaign.id)
        assert c is not None
        await uow.campaigns.set_status(
            c.id, CampaignStatus.PAUSED.value, expected_version=c.version
        )
    world.clock.advance(timedelta(days=4))
    await follow_up(world)(fu_job(world, 2))
    await _assert_held(world)

    async with world.factory() as uow:
        c = await uow.campaigns.get(world.campaign.id)
        assert c is not None
        await uow.campaigns.set_status(
            c.id, CampaignStatus.ACTIVE.value, expected_version=c.version
        )
    await follow_up(world)(fu_job(world, 2))
    assert await jobs(world, "outreach.draft") != [], "resuming must let the follow-up proceed"


# ====================================================================== dispatch
async def test_dispatch_outside_the_window_is_deferred_never_early_never_dropped(
    world: World,
) -> None:
    await pin_policy(
        world,
        timezone="America/New_York",
        sending_window_start_hour=9,
        sending_window_end_hour=17,
    )
    # the world clock starts Mon 2026-03-02 09:00Z = 04:00 EST: closed until 14:00Z
    m = await approved_and_queued(world)
    _, handler = send_handler(world)
    await handler(send_job(m))
    (queued,) = [x for x in await _msgs(world) if x.id == m.id]
    assert queued.status is M.QUEUED
    sends = await jobs(world, "outreach.send")
    reschedule = [j for j in sends if j["run_at"] == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)]
    # 09:00 EST Monday. Enqueue already scheduled the original job there; the dispatch-time
    # deferral adds one re-dispatch for the same instant. Nothing is due earlier.
    assert len(reschedule) == 2 and all(j["run_at"] >= reschedule[0]["run_at"] for j in sends)

    await handler(send_job(m))  # redelivery in the same closed window: idempotent re-enqueue
    assert len([j for j in await jobs(world, "outreach.send")]) == len(sends)

    world.clock.advance(datetime(2026, 3, 2, 13, 59, 59, tzinfo=UTC) - world.clock.now())
    await handler(send_job(m))
    assert [x.status for x in await _msgs(world) if x.id == m.id] == [M.QUEUED]  # still early

    world.clock.advance(timedelta(seconds=1))  # 14:00:00Z = 09:00 EST
    await handler(send_job(m))
    assert [x.status for x in await _msgs(world) if x.id == m.id] == [M.SENT]


async def _msgs(world: World):
    async with world.factory() as uow:
        return list(await uow.messages.list_for_sequence(world.sequence.id))


async def test_suppression_added_after_enqueue_still_stops_the_dispatch(world: World) -> None:
    await pin_policy(world)
    m = await approved_and_queued(world)
    async with world.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=world.channel_email,
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id=CORR,
        )
    _, handler = send_handler(world)
    await handler(send_job(m))
    (after,) = [x for x in await _msgs(world) if x.id == m.id]
    assert after.status is M.POLICY_BLOCKED
    assert await jobs(world, "outreach.follow_up") == []
