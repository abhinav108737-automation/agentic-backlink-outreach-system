"""MockEmailProvider: idempotency, delivery mode, scenario replies, failure routes."""

from datetime import UTC, datetime, timedelta

import pytest
from kb_contracts.enums import ReplyClass
from kb_contracts.providers import (
    EmailProvider,
    InvalidResponse,
    NotConfigured,
    OutboundEmail,
    ProviderUnavailable,
    RateLimited,
)
from kb_providers.email import MockEmailProvider, load_thread

T0 = datetime(2026, 3, 1, 12, tzinfo=UTC)
TO = "editor@editorial-desk.example.test"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        return self.t


def mail(key: str = "k1", to: str = TO, body: str = "Hello there") -> OutboundEmail:
    return OutboundEmail(idempotency_key=key, to=to, subject="Hi", body=body, thread_ref="th-1")


def test_satisfies_the_frozen_protocol_and_is_never_paid() -> None:
    p = MockEmailProvider()
    assert isinstance(p, EmailProvider)
    assert p.is_paid is False and p.name == "mock_email"


async def test_send_is_idempotent_by_key_and_delivers_once() -> None:
    p = MockEmailProvider()
    first = await p.send(mail())
    again = await p.send(mail())
    assert first.delivery_mode == "mock" and first.duplicate is False
    assert again.duplicate is True
    assert again.provider_message_id == first.provider_message_id
    assert len(p.deliveries) == 1
    assert p.lookup_sent("k1") == first and p.lookup_sent("nope") is None


async def test_distinct_keys_deliver_distinct_messages() -> None:
    p = MockEmailProvider()
    a, b = await p.send(mail("a")), await p.send(mail("b", body="Another"))
    assert a.provider_message_id != b.provider_message_id and len(p.deliveries) == 2


async def test_key_reuse_with_a_different_payload_is_rejected_not_dropped() -> None:
    p = MockEmailProvider()
    await p.send(mail())
    with pytest.raises(InvalidResponse):
        await p.send(mail(body="something else entirely"))
    assert len(p.deliveries) == 1


@pytest.mark.parametrize("bad", [mail(to="not-an-address"), mail(key=" ")])
async def test_malformed_messages_raise_the_taxonomy(bad: OutboundEmail) -> None:
    with pytest.raises(InvalidResponse):
        await MockEmailProvider().send(bad)


async def test_simulated_failures_use_only_the_shared_taxonomy() -> None:
    for exc in (ProviderUnavailable, RateLimited):
        p = MockEmailProvider(simulate=exc)
        with pytest.raises(exc):
            await p.send(mail())
        with pytest.raises(exc):
            await p.fetch_replies()
        assert (await p.health()).status == "down"
        assert p.deliveries == ()


async def test_simulated_failure_can_clear_after_n_calls_to_test_retries() -> None:
    p = MockEmailProvider(simulate=ProviderUnavailable, simulate_times=2)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await p.send(mail())
    assert (await p.send(mail())).duplicate is False
    assert len(p.deliveries) == 1


async def test_scenario_reply_appears_after_its_delay_and_is_stable() -> None:
    clock = Clock()
    p = MockEmailProvider(clock=clock, reply_scenarios={TO: "positive-interested"})
    receipt = await p.send(mail())
    assert await p.fetch_replies() == []  # 3600s has not elapsed
    clock.t = T0 + timedelta(hours=2)
    (reply,) = await p.fetch_replies()
    assert reply.from_address == TO and reply.thread_ref == "th-1"
    assert reply.received_at == T0 + timedelta(seconds=3600)
    assert receipt.provider_message_id.removeprefix("mock-")[:8] in reply.provider_message_id
    assert await p.fetch_replies() == [reply]  # polling twice yields the same reply
    assert await p.fetch_replies(since=reply.received_at) == []


async def test_scenario_trigger_counts_distinct_messages_to_the_recipient() -> None:
    clock = Clock()
    p = MockEmailProvider(clock=clock)
    p.script_reply(TO, "positive-interested")
    await p.send(mail("a"))
    await p.send(mail("a"))  # duplicate: not a second message
    clock.t = T0 + timedelta(days=1)
    assert len(await p.fetch_replies()) == 1
    await p.send(mail("b", body="second"))
    assert len(await p.fetch_replies()) == 1  # step-1 scenario does not fire for message 2


async def test_unscripted_recipients_never_reply() -> None:
    clock = Clock()
    p = MockEmailProvider(clock=clock)
    await p.send(mail())
    clock.t = T0 + timedelta(days=30)
    assert await p.fetch_replies() == []


def test_every_scenario_is_valid_c12_and_the_corpus_is_complete() -> None:
    p = MockEmailProvider()
    classes = {p.load_scenario(s).expected_class for s in p.list_scenarios()}
    assert classes == set(ReplyClass)  # positive .. bounce, plus unknown
    injected = [s for s in p.list_scenarios() if p.load_scenario(s).labels.get("prompt_injection")]
    assert injected == ["prompt-injection-positive"]


def test_unknown_or_malformed_scenarios_and_threads_fail_with_the_taxonomy() -> None:
    p = MockEmailProvider()
    with pytest.raises(NotConfigured):
        p.load_scenario("does-not-exist")
    with pytest.raises(InvalidResponse):
        p.load_scenario("../etc/passwd")
    with pytest.raises(NotConfigured):
        MockEmailProvider("/nonexistent-fixture-root").list_scenarios()


def test_thread_fixtures_load_typed() -> None:
    t = load_thread("negotiation-two-rounds")
    kinds = [type(m).__name__ for m in t["messages"]]
    assert kinds == ["OutboundEmail", "InboundEmail", "OutboundEmail", "InboundEmail"]
    with pytest.raises(NotConfigured):
        load_thread("missing")
