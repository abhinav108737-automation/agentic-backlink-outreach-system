"""The five message-level send gates plus the send-path rules, each with its trigger."""

from datetime import timedelta
from uuid import uuid4

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    MessageStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.policy import BlockCode, PolicyCheckRequest
from kb_core.audit.service import OPERATOR
from kb_core.errors import InvalidInput
from kb_core.settings.service import SettingsService
from kb_core.suppression.service import SuppressionService
from kb_core.testing.scenario import Scenario

NOW = None


def mreq(w, action="enqueue_send", mode="mock"):
    return PolicyCheckRequest(
        action=action,
        campaign_id=w.campaign.id,
        subject_type="message",
        subject_id=w.message.id,
        context={"delivery_mode": mode},
        correlation_id="c1",
    )


def body(**kw):
    return PolicyBody(require_approval_for_send=False, **kw)


async def check(sc, w, **kw):
    return await sc.policy.check(mreq(w, **kw))


async def test_clean_message_is_allowed():
    sc = Scenario(body())
    w = await sc.world()
    d = await check(sc, w)
    assert d.allowed and d.block_codes == ()


# -- SENDER_INVALID ---------------------------------------------------------------------------
async def test_sender_invalid_by_default_on_a_fresh_database():
    sc = Scenario(body())
    w = await sc.world(sender=False)
    d = await check(sc, w)
    assert d.block_codes == (BlockCode.SENDER_INVALID,)
    assert d.detail["sender_invalid"] == "no_sender_configured"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"enabled": False}, "sender_account_disabled"),
        ({"provider_type": "search"}, "sender_account_not_email"),
    ],
)
async def test_sender_invalid_for_disabled_or_non_email_account(over, reason):
    sc = Scenario(body())
    w = await sc.world(sender=False)
    await sc.sender(w.campaign, **over)
    d = await check(sc, w)
    assert BlockCode.SENDER_INVALID in d.block_codes and d.detail["sender_invalid"] == reason


async def test_sender_invalid_when_referenced_account_is_missing():
    sc = Scenario(body())
    w = await sc.world(sender=False)
    sc.store.campaigns[w.campaign.id] = sc.store.campaigns[w.campaign.id].model_copy(
        update={"sender_provider_account_id": uuid4()}
    )
    d = await check(sc, w)
    assert d.detail["sender_invalid"] == "sender_account_missing"


async def test_enabled_paid_email_account_is_explicitly_enabled_so_it_passes():
    sc = Scenario(body())
    w = await sc.world(sender=False)
    await sc.sender(w.campaign, is_paid=True)  # enabled=True is the explicit opt-in
    assert (await check(sc, w)).allowed


# -- DUPLICATE_STEP ---------------------------------------------------------------------------
async def test_duplicate_step_when_a_delivered_version_already_occupies_the_step():
    sc = Scenario(body())
    w = await sc.world(status=MessageStatus.DELIVERED)  # v1 left the system and is terminal
    v2 = await sc.message(w.sequence, step_no=1, message_version=2, status=MessageStatus.APPROVED)
    w2 = type(w)(w.campaign, w.prospect, w.channel, w.sequence, v2)
    d = await check(sc, w2)
    assert BlockCode.DUPLICATE_STEP in d.block_codes and d.detail["duplicate_step"] == 1


async def test_duplicate_step_when_another_live_message_holds_the_step():
    sc = Scenario(body())
    w = await sc.world()
    # bypass the DB constraint to prove the gate agrees with it rather than relying on it
    twin = w.message.model_copy(
        update={"id": uuid4(), "message_version": 2, "idempotency_key": "x"}
    )
    sc.store.messages[twin.id] = twin
    assert BlockCode.DUPLICATE_STEP in (await check(sc, w)).block_codes


async def test_duplicate_step_when_the_message_itself_already_left():
    sc = Scenario(body())
    w = await sc.world(status=MessageStatus.SENT)
    assert BlockCode.DUPLICATE_STEP in (await check(sc, w, action="send")).block_codes


async def test_blocked_or_cancelled_earlier_version_does_not_occupy_the_step():
    sc = Scenario(body())
    w = await sc.world(status=MessageStatus.POLICY_BLOCKED)
    v2 = await sc.message(w.sequence, step_no=1, message_version=2, status=MessageStatus.APPROVED)
    w2 = type(w)(w.campaign, w.prospect, w.channel, w.sequence, v2)
    assert BlockCode.DUPLICATE_STEP not in (await check(sc, w2)).block_codes


# -- DUPLICATE_CONTACT ------------------------------------------------------------------------
async def test_duplicate_contact_when_another_sequence_has_a_live_message_to_the_channel():
    sc = Scenario(body())
    w = await sc.world()
    other = await sc.sequence(w.prospect, w.channel)  # same channel, different sequence
    await sc.message(other, status=MessageStatus.QUEUED, idempotency_key="other")
    d = await check(sc, w)
    assert BlockCode.DUPLICATE_CONTACT in d.block_codes


async def test_follow_up_in_the_same_sequence_is_not_a_duplicate_contact():
    sc = Scenario(body())
    w = await sc.world()
    await sc.message(w.sequence, step_no=2, status=MessageStatus.SENT, idempotency_key="s2")
    assert BlockCode.DUPLICATE_CONTACT not in (await check(sc, w)).block_codes


async def test_finished_message_to_the_channel_is_not_a_duplicate_contact():
    sc = Scenario(body())
    w = await sc.world()
    other = await sc.sequence(w.prospect, w.channel)
    await sc.message(other, status=MessageStatus.DELIVERED, idempotency_key="done")
    assert BlockCode.DUPLICATE_CONTACT not in (await check(sc, w)).block_codes


# -- SEND_LIMIT_EXCEEDED ----------------------------------------------------------------------
async def _sent_elsewhere(sc, w, *, same_channel=False, same_domain=False):
    prospect = w.prospect
    if not same_domain:
        prospect = await sc.prospect(w.campaign, domain="other.org")
    ch = w.channel if same_channel else await sc.channel(prospect, email="x@other.org")
    seq = await sc.sequence(prospect, ch)
    return await sc.message(seq, status=MessageStatus.SENT, idempotency_key=f"s-{uuid4()}")


async def test_daily_limit():
    sc = Scenario(body(daily_send_limit=1, per_domain_send_limit=9, per_contact_send_limit=9))
    w = await sc.world()
    assert (await check(sc, w)).allowed
    await _sent_elsewhere(sc, w)
    d = await check(sc, w)
    assert d.block_codes == (BlockCode.SEND_LIMIT_EXCEEDED,) and d.detail["limits_exceeded"] == [
        "daily"
    ]


async def test_per_domain_limit():
    sc = Scenario(body(daily_send_limit=99, per_domain_send_limit=1, per_contact_send_limit=9))
    w = await sc.world()
    other_channel = await sc.channel(w.prospect, email="second@example.com")
    seq2 = await sc.sequence(w.prospect, other_channel)
    await sc.message(seq2, status=MessageStatus.SENT, idempotency_key="d")
    assert (await check(sc, w)).detail["limits_exceeded"] == ["per_domain"]


async def test_per_contact_limit():
    sc = Scenario(body(daily_send_limit=99, per_domain_send_limit=9, per_contact_send_limit=1))
    w = await sc.world()
    other = await sc.sequence(w.prospect, w.channel)
    await sc.message(other, status=MessageStatus.DELIVERED, idempotency_key="c")
    assert (await check(sc, w)).detail["limits_exceeded"] == ["per_contact"]


async def test_sends_outside_the_24h_window_do_not_count():
    sc = Scenario(body(daily_send_limit=1, per_domain_send_limit=9, per_contact_send_limit=9))
    w = await sc.world()
    await _sent_elsewhere(sc, w)
    sc.store.clock.advance(timedelta(hours=25))
    assert (await check(sc, w)).allowed


async def test_zero_limit_blocks_everything():
    sc = Scenario(body(daily_send_limit=0))
    w = await sc.world()
    assert BlockCode.SEND_LIMIT_EXCEEDED in (await check(sc, w)).block_codes


# -- UNSUPPORTED_CLAIM ------------------------------------------------------------------------
async def _evidence(sc, prospect):
    async with sc.factory() as uow:
        items = await uow.evidence.record_many(
            prospect.id,
            [
                {
                    "source_url": "https://example.com/a",
                    "excerpt": "e",
                    "content_hash": "h",
                    "kind": "page",
                    "extracted_at": sc.store.clock.now(),
                    "extractor": "t",
                }
            ],
        )
    return items[0]


async def test_claim_with_persisted_evidence_passes():
    sc = Scenario(body())
    w = await sc.world()
    ev = await _evidence(sc, w.prospect)
    sc.store.messages[w.message.id] = w.message.model_copy(update={"claims": {"c": (ev.id,)}})
    assert (await check(sc, w)).allowed


@pytest.mark.parametrize("kind", ["unknown_id", "no_evidence", "other_prospect"])
async def test_unsupported_claim_blocks_not_warns(kind):
    sc = Scenario(body())
    w = await sc.world()
    if kind == "unknown_id":
        ids = (uuid4(),)
    elif kind == "no_evidence":
        ids = ()
    else:
        other = await sc.prospect(w.campaign, domain="elsewhere.org")
        ids = ((await _evidence(sc, other)).id,)
    sc.store.messages[w.message.id] = w.message.model_copy(update={"claims": {"c": ids}})
    d = await check(sc, w)
    assert d.block_codes == (BlockCode.UNSUPPORTED_CLAIM,) and not d.allowed
    assert d.detail["unsupported_claim_indexes"] == [0]


# -- kill switch, suppression after approval, real send, no short-circuit ---------------------
async def test_kill_switch_blocks_a_send():
    sc = Scenario(body())
    w = await sc.world()
    await SettingsService(sc.factory).set_kill_switch(
        active=True, reason="stop", actor=OPERATOR, correlation_id="k"
    )
    for action in ("enqueue_send", "send"):
        assert BlockCode.KILL_SWITCH_ACTIVE in (await check(sc, w, action=action)).block_codes


async def test_suppression_added_after_approval_blocks_both_checks():
    sc = Scenario(body())
    w = await sc.world()
    assert (await check(sc, w, action="send")).allowed
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="editor@example.com",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    for action in ("enqueue_send", "send"):  # the request context never named the address
        assert BlockCode.SUPPRESSED in (await check(sc, w, action=action)).block_codes


async def test_real_delivery_mode_is_refused_while_real_send_is_off():
    sc = Scenario(body())
    w = await sc.world()
    assert BlockCode.REAL_SEND_DISABLED in (await check(sc, w, mode="real")).block_codes


async def test_every_triggered_code_is_reported_together():
    sc = Scenario(body(daily_send_limit=0))
    w = await sc.world(sender=False)
    sc.store.messages[w.message.id] = w.message.model_copy(update={"claims": {"c": ()}})
    await SettingsService(sc.factory).set_kill_switch(
        active=True, reason=None, actor=OPERATOR, correlation_id="k"
    )
    got = set((await check(sc, w, mode="real")).block_codes)
    assert {
        BlockCode.KILL_SWITCH_ACTIVE,
        BlockCode.SENDER_INVALID,
        BlockCode.SEND_LIMIT_EXCEEDED,
        BlockCode.UNSUPPORTED_CLAIM,
        BlockCode.REAL_SEND_DISABLED,
    } <= got


async def test_message_of_another_campaign_is_rejected():
    sc = Scenario(body())
    w = await sc.world()
    other = await sc.campaign()
    r = mreq(w).model_copy(update={"campaign_id": other.id})
    with pytest.raises(InvalidInput):
        await sc.policy.check(r)
