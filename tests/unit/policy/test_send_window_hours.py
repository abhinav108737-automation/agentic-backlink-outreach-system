"""`PolicyBody.send_window_hours` (not a constant) drives all three send-limit checks."""

from datetime import timedelta
from uuid import uuid4

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import MessageStatus
from kb_contracts.policy import BlockCode, PolicyCheckRequest
from kb_core.testing.scenario import Scenario


def body(hours: int, **kw):
    return PolicyBody(
        require_approval_for_send=False,
        send_window_hours=hours,
        daily_send_limit=1,
        per_domain_send_limit=1,
        per_contact_send_limit=1,
        **kw,
    )


async def decide(sc, w):
    return await sc.policy.check(
        PolicyCheckRequest(
            action="send",
            campaign_id=w.campaign.id,
            subject_type="message",
            subject_id=w.message.id,
            context={"delivery_mode": "mock"},
            correlation_id="c",
        )
    )


async def sent_to_everyone(sc, w):
    """One DELIVERED message in the same campaign, domain and contact channel as `w`."""
    other = await sc.sequence(w.prospect, w.channel)
    await sc.message(other, status=MessageStatus.DELIVERED, idempotency_key=f"s-{uuid4()}")


@pytest.mark.parametrize("hours", [6, 24, 72])
async def test_the_limit_window_moves_with_the_policy(hours):
    sc = Scenario(body(hours))
    w = await sc.world()
    await sent_to_everyone(sc, w)
    sc.store.clock.advance(timedelta(hours=hours - 1))
    d = await decide(sc, w)
    assert BlockCode.SEND_LIMIT_EXCEEDED in d.block_codes
    assert d.detail["limits_exceeded"] == ["daily", "per_domain", "per_contact"]  # no short-circuit
    sc.store.clock.advance(timedelta(hours=2))  # now older than the policy window
    assert BlockCode.SEND_LIMIT_EXCEEDED not in (await decide(sc, w)).block_codes


async def test_a_six_hour_window_forgets_a_send_that_a_24_hour_window_still_counts():
    outcomes = {}
    for hours in (6, 24):
        sc = Scenario(body(hours))
        w = await sc.world()
        await sent_to_everyone(sc, w)
        sc.store.clock.advance(timedelta(hours=7))
        outcomes[hours] = BlockCode.SEND_LIMIT_EXCEEDED in (await decide(sc, w)).block_codes
    assert outcomes == {6: False, 24: True}
