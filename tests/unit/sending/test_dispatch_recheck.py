"""begin_send re-evaluates everything immediately before dispatch (CCR-004 requirement 4).

Each case builds a QUEUED message that was fine at enqueue, then changes the world, then
dispatches. Permanent causes are BLOCKED; transient ones are DEFERRED; the window is DEFERRED
with `not_before`. No seventh outcome exists.
"""

from datetime import UTC, datetime

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    CampaignStatus,
    MessageStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.policy import BlockCode
from kb_core.approvals.service import ApprovalService
from kb_core.audit.service import OPERATOR
from kb_core.scheduling.clock import FakeClock
from kb_core.sending.service import BeginOutcome, SendService
from kb_core.settings.service import SettingsService
from kb_core.suppression.service import SuppressionService
from kb_core.testing.scenario import Scenario

M = MessageStatus


def svc(sc):
    return SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    )


def lax(**kw):
    return Scenario(PolicyBody(require_approval_for_send=False, **kw))


async def begin(sc, w):
    return await svc(sc).begin_send(w.message.id, correlation_id="c")


async def test_suppression_landing_after_enqueue_blocks_permanently():
    sc = lax()
    w = await sc.world(status=M.QUEUED)
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="editor@example.com",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.BLOCKED and BlockCode.SUPPRESSED in res.decision.block_codes
    assert sc.store.messages[w.message.id].status is M.POLICY_BLOCKED


async def test_campaign_pause_after_enqueue_defers():
    sc = lax()
    w = await sc.world(status=M.QUEUED)
    sc.store.campaigns[w.campaign.id] = sc.store.campaigns[w.campaign.id].model_copy(
        update={"status": CampaignStatus.PAUSED}
    )
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.CAMPAIGN_PAUSED in res.decision.block_codes
    assert sc.store.messages[w.message.id].status is M.QUEUED


async def test_kill_switch_after_enqueue_defers():
    sc = lax()
    w = await sc.world(status=M.QUEUED)
    await SettingsService(sc.factory).set_kill_switch(
        active=True, reason="stop", actor=OPERATOR, correlation_id="k"
    )
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.KILL_SWITCH_ACTIVE in res.decision.block_codes


async def test_approval_is_re_evaluated_at_dispatch():
    sc = Scenario()  # default policy requires approval; this QUEUED message has none
    w = await sc.world(status=M.QUEUED)
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.APPROVAL_REQUIRED in res.decision.block_codes


async def test_sender_eligibility_is_re_evaluated_at_dispatch():
    sc = lax()
    w = await sc.world(status=M.QUEUED)
    account_id = sc.store.campaigns[w.campaign.id].sender_provider_account_id
    sc.store.provider_accounts[account_id]["enabled"] = False  # disabled after enqueue
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.DEFERRED
    assert BlockCode.SENDER_INVALID in res.decision.block_codes


async def test_an_idempotency_key_owned_by_another_message_is_never_dispatched():
    sc = lax()
    w = await sc.world(status=M.QUEUED)
    other = await sc.sequence(w.prospect, w.channel)
    twin = await sc.message(other, status=M.SENT, idempotency_key="other-key")
    # corrupt the world: this message now claims the other message's key
    sc.store.messages[w.message.id] = sc.store.messages[w.message.id].model_copy(
        update={"idempotency_key": twin.idempotency_key}
    )
    msgs = sc.store.messages  # the fake answers the first match, so put the owner first
    sc.store.messages = {twin.id: msgs[twin.id], **{k: v for k, v in msgs.items() if k != twin.id}}
    res = await begin(sc, w)
    assert res.outcome is BeginOutcome.NOT_SENDABLE
    assert sc.store.messages[w.message.id].status is M.QUEUED


# -- sending window ---------------------------------------------------------------------------
def windowed(start=9, end=17, tz="America/New_York"):
    sc = Scenario(
        PolicyBody(
            require_approval_for_send=False,
            timezone=tz,
            sending_window_start_hour=start,
            sending_window_end_hour=end,
        )
    )
    return sc


async def test_outside_the_window_is_deferred_to_the_next_open_instant_not_sent():
    sc = windowed()
    sc.store.clock = FakeClock(datetime(2026, 1, 5, 3, 0, tzinfo=UTC))  # 22:00 EST Sun
    w = await sc.world(status=M.QUEUED)
    res = await SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    ).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.DEFERRED
    assert res.not_before == datetime(2026, 1, 5, 14, 0, tzinfo=UTC)  # 09:00 EST Monday
    assert sc.store.messages[w.message.id].status is M.QUEUED
    assert any(r["action"] == "send.deferred_outside_window" for r in sc.store.audit)


async def test_inside_the_window_proceeds():
    sc = windowed()
    sc.store.clock = FakeClock(datetime(2026, 1, 5, 15, 0, tzinfo=UTC))  # 10:00 EST
    w = await sc.world(status=M.QUEUED)
    res = await SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    ).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.PROCEED and res.not_before is None


@pytest.mark.parametrize("hour_utc", [13, 14])
async def test_never_early_by_a_single_instant(hour_utc):
    sc = windowed()
    sc.store.clock = FakeClock(datetime(2026, 1, 5, hour_utc, 59, 59, tzinfo=UTC))
    w = await sc.world(status=M.QUEUED)
    res = await SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    ).begin_send(w.message.id, correlation_id="c")
    # 13:59:59Z = 08:59:59 EST (closed), 14:59:59Z = 09:59:59 EST (open)
    expected = BeginOutcome.DEFERRED if hour_utc == 13 else BeginOutcome.PROCEED
    assert res.outcome is expected


async def test_a_permanent_block_is_reported_even_outside_the_window():
    sc = windowed()
    sc.store.clock = FakeClock(datetime(2026, 1, 5, 3, 0, tzinfo=UTC))
    w = await sc.world(status=M.QUEUED)
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="editor@example.com",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    res = await SendService(
        sc.factory, sc.policy, sc.transitions, ApprovalService(sc.factory), sc.store.clock
    ).begin_send(w.message.id, correlation_id="c")
    assert res.outcome is BeginOutcome.BLOCKED
