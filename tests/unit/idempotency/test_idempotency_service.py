import pytest
from kb_core.audit.service import OPERATOR
from kb_core.campaigns.service import CampaignService
from kb_core.errors import IdempotencyInFlight, IdempotencyKeyReused, InvalidInput
from kb_core.idempotency.service import IdempotencyService, request_hash
from kb_core.testing.scenario import Scenario


def test_hash_ignores_key_order():
    assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


async def test_replay_does_not_repeat_the_operation():
    sc = Scenario()
    svc, calls = IdempotencyService(), []

    async def op() -> str:
        calls.append(1)
        return "thing:1"

    for expected_replay in (False, True, True):
        async with sc.factory() as uow:
            out = await svc.execute(uow, scope="s", key="k", payload={"x": 1}, operation=op)
        assert out.ref == "thing:1" and out.replayed is expected_replay
    assert calls == [1]


async def test_same_key_different_body_is_rejected():
    sc = Scenario()
    svc = IdempotencyService()

    async def op() -> str:
        return "r"

    async with sc.factory() as uow:
        await svc.execute(uow, scope="s", key="k", payload={"x": 1}, operation=op)
    async with sc.factory() as uow:
        with pytest.raises(IdempotencyKeyReused):
            await svc.execute(uow, scope="s", key="k", payload={"x": 2}, operation=op)


async def test_scopes_are_independent():
    sc = Scenario()
    svc = IdempotencyService()

    async def op() -> str:
        return "r"

    async with sc.factory() as uow:
        a = await svc.execute(uow, scope="a", key="k", payload=1, operation=op)
        b = await svc.execute(uow, scope="b", key="k", payload=1, operation=op)
    assert not a.replayed and not b.replayed


async def test_failed_operation_releases_the_key_for_retry():
    sc = Scenario()
    svc = IdempotencyService()

    async def boom() -> str:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        async with sc.factory() as uow:
            await svc.execute(uow, scope="s", key="k", payload=1, operation=boom)

    async def ok() -> str:
        return "done"

    async with sc.factory() as uow:
        out = await svc.execute(uow, scope="s", key="k", payload=1, operation=ok)
    assert out.ref == "done" and not out.replayed


async def test_claimed_without_outcome_is_in_flight():
    sc = Scenario()
    async with sc.factory() as uow:
        await uow.idempotency.claim(scope="s", key="k", request_hash=request_hash(1))
    async with sc.factory() as uow:
        with pytest.raises(IdempotencyInFlight):
            await IdempotencyService().execute(
                uow,
                scope="s",
                key="k",
                payload=1,
                operation=lambda: None,  # type: ignore[arg-type]
            )


@pytest.mark.parametrize("key", ["", "  ", "x" * 201])
async def test_bad_keys(key):
    sc = Scenario()
    async with sc.factory() as uow:
        with pytest.raises(InvalidInput):
            await IdempotencyService().execute(
                uow,
                scope="s",
                key=key,
                payload=1,
                operation=lambda: None,  # type: ignore[arg-type]
            )


async def test_campaign_create_with_key_creates_exactly_one_campaign():
    sc = Scenario()
    svc = CampaignService(sc.factory, sc.transitions)
    kw = dict(
        name="A",
        goals={"k": 1},
        policy_version_id=None,
        actor=OPERATOR,
        correlation_id="c",
        idempotency_key="key-1",
    )
    first, r1 = await svc.create(**kw)
    second, r2 = await svc.create(**kw)
    assert (r1, r2) == (False, True) and first.id == second.id
    assert len(sc.store.campaigns) == 1
    assert len([a for a in sc.store.audit if a["action"] == "campaign.created"]) == 1
    with pytest.raises(IdempotencyKeyReused):
        await svc.create(**{**kw, "name": "B"})
