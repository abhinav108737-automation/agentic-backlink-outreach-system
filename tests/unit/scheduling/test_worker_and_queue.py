"""Worker and queue behaviour using FakeClock and the fake job repository."""

from datetime import UTC, datetime, timedelta

import pytest
from kb_contracts.enums import JobStatus
from kb_core.scheduling.clock import FakeClock, SystemClock
from kb_core.scheduling.queue import UowJobQueue
from kb_core.scheduling.worker import JobWorker
from kb_core.testing.fakes import FakeUowFactory

T0 = datetime(2026, 5, 1, 9, tzinfo=UTC)


def setup(handlers, **kw):
    f = FakeUowFactory()
    f.store.clock = FakeClock(T0)
    return f, f.store.clock, JobWorker(f, f.store.clock, handlers, **kw)


def status(f, key):
    return next(r for r in f.store.jobs.values() if r["idempotency_key"] == key)


def test_fake_clock_is_aware_and_monotonic():
    c = FakeClock(T0)
    assert c.advance(5) == T0 + timedelta(seconds=5)
    with pytest.raises(ValueError):
        c.advance(-1)
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))
    assert SystemClock().now().tzinfo is not None


async def test_enqueue_is_idempotent_and_rejects_naive_run_at_and_cancel_works():
    f = FakeUowFactory()
    q = UowJobQueue(f)
    a = await q.enqueue("t", {}, T0, "k1")
    b = await q.enqueue("t", {}, T0, "k1")
    assert a == b and len(f.store.jobs) == 1
    with pytest.raises(ValueError):
        await q.enqueue("t", {}, datetime(2026, 1, 1), "k2")
    with pytest.raises(ValueError):
        await q.enqueue(" ", {}, T0, "k3")
    assert await q.cancel("k1") is True and await q.cancel("k1") is False
    assert status(f, "k1")["status"] is JobStatus.CANCELLED


async def test_due_job_runs_future_job_waits():
    seen = []

    async def h(job):
        seen.append(job.idempotency_key)

    f, clock, w = setup({"t": h})
    q = UowJobQueue(f)
    await q.enqueue("t", {}, T0, "now")
    await q.enqueue("t", {}, T0 + timedelta(hours=1), "later")
    r = await w.drain_once()
    assert (r.claimed, r.succeeded) == (1, 1) and seen == ["now"]
    clock.advance(timedelta(hours=1))
    assert (await w.drain_once()).succeeded == 1 and seen == ["now", "later"]


async def test_retry_bounds_backoff_then_dead_letter():
    async def boom(job):
        raise RuntimeError("nope")

    f, clock, w = setup({"t": boom})
    async with f() as uow:
        await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=T0, idempotency_key="k", max_attempts=3
        )
    r = await w.drain_once()
    row = status(f, "k")
    assert r.failed == 1 and row["status"] is JobStatus.RETRY_WAIT and row["attempts"] == 1
    assert row["last_error"].startswith("RuntimeError")
    # still in backoff: nothing is claimed at the same instant
    assert (await w.drain_once()).claimed == 0
    for expected_attempts, final in ((2, JobStatus.RETRY_WAIT), (3, JobStatus.DEAD)):
        clock.advance(timedelta(hours=2))  # beyond any bounded backoff
        await w.drain_once()
        row = status(f, "k")
        assert row["attempts"] == expected_attempts and row["status"] is final
    clock.advance(timedelta(days=1))
    assert (await w.drain_once()).claimed == 0  # dead never runs again


async def test_backoff_is_bounded_at_one_hour():
    from kb_core.testing.fakes import _backoff

    assert _backoff(1) == timedelta(seconds=30) and _backoff(2) == timedelta(seconds=60)
    assert _backoff(50) == timedelta(hours=1)


async def test_unhandled_job_type_dead_letters_immediately():
    f, _, w = setup({})
    await UowJobQueue(f).enqueue("mystery", {}, T0, "k")
    r = await w.drain_once()
    assert r.unhandled == 1 and status(f, "k")["status"] is JobStatus.DEAD


async def test_lease_expiry_reclaims_then_dead_when_attempts_spent():
    f, clock, w = setup({}, lease_seconds=60)
    async with f() as uow:
        await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=T0, idempotency_key="k", max_attempts=2
        )
        await uow.jobs.claim_due(now=T0, lease_owner="crashed", lease_seconds=60)
    assert status(f, "k")["status"] is JobStatus.RUNNING
    async with f() as uow:
        assert await uow.jobs.reclaim_expired(now=T0 + timedelta(seconds=30)) == 0  # lease held
    clock.advance(61)
    async with f() as uow:
        assert await uow.jobs.reclaim_expired(now=clock.now()) == 1
    row = status(f, "k")
    assert row["status"] is JobStatus.SCHEDULED and row["attempts"] == 1
    async with f() as uow:
        await uow.jobs.claim_due(now=clock.now(), lease_owner="crashed2", lease_seconds=60)
    clock.advance(61)
    async with f() as uow:
        await uow.jobs.reclaim_expired(now=clock.now())
    assert status(f, "k")["status"] is JobStatus.DEAD


async def test_one_job_per_concurrency_key_per_claim():
    ran = []

    async def h(job):
        ran.append(job.idempotency_key)

    f, _, w = setup({"t": h}, batch_size=10)
    q = UowJobQueue(f)
    await q.enqueue("t", {}, T0, "a", concurrency_key="dom")
    await q.enqueue("t", {}, T0, "b", concurrency_key="dom")
    await q.enqueue("t", {}, T0, "c")
    assert (await w.drain_once()).claimed == 2
    assert (await w.drain_once()).claimed == 1  # the second 'dom' job waits for the next pass
    assert sorted(ran) == ["a", "b", "c"]


async def test_run_iterations_is_bounded_and_stops_when_idle():
    async def h(job):
        return None

    f, _, w = setup({"t": h}, batch_size=1)
    q = UowJobQueue(f)
    for i in range(5):
        await q.enqueue("t", {}, T0, f"k{i}")
    assert (await w.run_iterations(3)).claimed == 3  # bounded by n, not by backlog
    assert (await w.run_iterations(50)).claimed == 2  # stops at the first empty pass
    for bad in (0, 1001):
        with pytest.raises(ValueError):
            await w.run_iterations(bad)
    with pytest.raises(ValueError):
        JobWorker(f, f.store.clock, {}, batch_size=0)


async def test_lost_lease_is_reported_not_raised():
    f, clock, _ = setup({})

    async def slow(job):
        clock.advance(10_000)
        async with f() as uow:  # another worker reclaims while this handler runs
            await uow.jobs.reclaim_expired(now=clock.now())

    w = JobWorker(f, clock, {"t": slow}, lease_seconds=60)
    await UowJobQueue(f).enqueue("t", {}, T0, "k")
    r = await w.drain_once()
    assert r.lost_lease == 1 and r.succeeded == 0
