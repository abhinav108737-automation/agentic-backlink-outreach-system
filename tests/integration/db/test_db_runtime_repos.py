"""Concurrency (idempotency, job queue), schema drift, and basic round-trips."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from kb_contracts.enums import ApprovalStatus, JobStatus
from kb_core.db import tables
from kb_core.db.protocols import OptimisticLockError
from kb_core.db.repos_runtime import ApprovalAlreadyDecided, InvalidJobTransition
from kb_core.db.uow import SqlUnitOfWorkFactory
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.db
NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)


async def test_idempotency_race_exactly_one_winner(factory: SqlUnitOfWorkFactory) -> None:  # (f)
    a, b = factory(), factory()
    await a.__aenter__()
    await b.__aenter__()
    assert await a.idempotency.claim(scope="s", key="k", request_hash="h") is True
    contender = asyncio.create_task(b.idempotency.claim(scope="s", key="k", request_hash="h"))
    await asyncio.sleep(0.3)
    assert not contender.done()  # genuinely blocked on A's uncommitted unique entry
    await a.__aexit__(None, None, None)  # A commits
    assert await asyncio.wait_for(contender, 5) is False  # B lost
    await b.__aexit__(None, None, None)
    async with factory() as uow:
        got = await uow.idempotency.get(scope="s", key="k")
        assert got is not None and got["request_hash"] == "h" and got["response_ref"] is None
        await uow.idempotency.record_response(scope="s", key="k", response_ref="ref1")
        got = await uow.idempotency.get(scope="s", key="k")
        assert got is not None and got["response_ref"] == "ref1"
        assert await uow.idempotency.get(scope="s", key="nope") is None
        with pytest.raises(LookupError):
            await uow.idempotency.record_response(scope="s", key="nope", response_ref="r")


async def test_idempotency_loser_wins_if_first_rolls_back(factory: SqlUnitOfWorkFactory) -> None:
    a, b = factory(), factory()
    await a.__aenter__()
    await b.__aenter__()
    assert await a.idempotency.claim(scope="s", key="k2", request_hash="h")
    contender = asyncio.create_task(b.idempotency.claim(scope="s", key="k2", request_hash="h"))
    await asyncio.sleep(0.2)
    await a.__aexit__(RuntimeError, RuntimeError("boom"), None)  # A rolls back
    assert await asyncio.wait_for(contender, 5) is True
    await b.__aexit__(None, None, None)


async def _enqueue(factory: SqlUnitOfWorkFactory, n: int) -> list[str]:
    keys = [f"job-{i}" for i in range(n)]
    async with factory() as uow:
        for i, k in enumerate(keys):
            await uow.jobs.enqueue(
                job_type="t",
                payload={"i": i},
                run_at=NOW - timedelta(minutes=n - i),
                idempotency_key=k,
            )
    return keys


async def test_job_claim_race_skip_locked(factory: SqlUnitOfWorkFactory) -> None:  # (g)
    await _enqueue(factory, 4)
    a, b = factory(), factory()
    await a.__aenter__()
    await b.__aenter__()
    first = await a.jobs.claim_due(now=NOW, lease_owner="wa", lease_seconds=60, limit=2)
    # A's transaction is still open and holds row locks. B must NOT block and must get the rest.
    second = await asyncio.wait_for(
        b.jobs.claim_due(now=NOW, lease_owner="wb", lease_seconds=60, limit=4), 5
    )
    assert len(first) == 2 and len(second) == 2
    assert {j.id for j in first}.isdisjoint({j.id for j in second})
    assert all(j.status is JobStatus.RUNNING for j in [*first, *second])
    await a.__aexit__(None, None, None)
    await b.__aexit__(None, None, None)


async def test_job_claim_many_workers_never_overlap(factory: SqlUnitOfWorkFactory) -> None:
    await _enqueue(factory, 12)

    async def worker(name: str) -> list[str]:
        async with factory() as uow:
            return [
                str(j.id)
                for j in await uow.jobs.claim_due(
                    now=NOW, lease_owner=name, lease_seconds=60, limit=3
                )
            ]

    results = await asyncio.gather(*[worker(f"w{i}") for i in range(6)])
    flat = [j for r in results for j in r]
    assert len(flat) == len(set(flat)) == 12


async def test_job_lifecycle(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        j1 = await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=NOW, idempotency_key="k1", max_attempts=2
        )
        again = await uow.jobs.enqueue(
            job_type="t", payload={"x": 1}, run_at=NOW, idempotency_key="k1"
        )
        assert again.id == j1.id and again.payload == {}  # idempotent enqueue
        future = await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=NOW + timedelta(hours=1), idempotency_key="k2"
        )
        claimed = await uow.jobs.claim_due(now=NOW, lease_owner="w", lease_seconds=30)
        assert [c.id for c in claimed] == [j1.id]  # future job not claimed
        with pytest.raises(InvalidJobTransition):
            await uow.jobs.complete(
                future.id, status=JobStatus.SUCCEEDED, last_error=None
            )  # not RUNNING
        with pytest.raises(InvalidJobTransition):
            await uow.jobs.complete(j1.id, status=JobStatus.CANCELLED, last_error=None)
        await uow.jobs.complete(j1.id, status=JobStatus.RETRY_WAIT, last_error="e1")
        row = (
            (
                await uow.session.execute(
                    sa.select(tables.scheduled_jobs).where(tables.scheduled_jobs.c.id == j1.id)
                )
            )
            .mappings()
            .one()
        )
        assert row["status"] == "RETRY_WAIT" and row["attempts"] == 1 and row["lease_owner"] is None
        # backoff elapsed -> promoted back to SCHEDULED and claimable again
        later = datetime.now(UTC) + timedelta(days=1)  # backoff is computed on the DB clock
        again_claim = await uow.jobs.claim_due(
            now=later, lease_owner="w", lease_seconds=30, limit=5
        )
        assert j1.id in [c.id for c in again_claim]
        await uow.jobs.complete(
            j1.id, status=JobStatus.RETRY_WAIT, last_error="e2"
        )  # attempts 2 == max
        row = (
            await uow.session.execute(
                sa.select(tables.scheduled_jobs.c.status).where(tables.scheduled_jobs.c.id == j1.id)
            )
        ).scalar_one()
        assert row == "DEAD"  # retry-bounded


async def test_job_cancel_and_reclaim(factory: SqlUnitOfWorkFactory) -> None:
    sid = uuid4()
    async with factory() as uow:
        await uow.jobs.enqueue(
            job_type="t",
            payload={},
            run_at=NOW,
            idempotency_key="a",
            subject_type="seq",
            subject_id=sid,
        )
        await uow.jobs.enqueue(
            job_type="t",
            payload={},
            run_at=NOW,
            idempotency_key="b",
            subject_type="seq",
            subject_id=sid,
        )
        await uow.jobs.enqueue(job_type="t", payload={}, run_at=NOW, idempotency_key="c")
        assert await uow.jobs.cancel(idempotency_key="c") is True
        assert await uow.jobs.cancel(idempotency_key="c") is False
        assert await uow.jobs.cancel_for_subject(subject_type="seq", subject_id=sid) == 2
        await uow.jobs.enqueue(job_type="t", payload={}, run_at=NOW, idempotency_key="d")
        (job,) = await uow.jobs.claim_due(now=NOW, lease_owner="w", lease_seconds=10)
        assert await uow.jobs.reclaim_expired(now=NOW + timedelta(seconds=5)) == 0
        assert await uow.jobs.reclaim_expired(now=NOW + timedelta(seconds=11)) == 1
        status, attempts = (
            await uow.session.execute(
                sa.select(tables.scheduled_jobs.c.status, tables.scheduled_jobs.c.attempts).where(
                    tables.scheduled_jobs.c.id == job.id
                )
            )
        ).one()
        assert (status, attempts) == ("SCHEDULED", 1)


async def test_job_concurrency_key_serialises(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        for k in ("x1", "x2"):
            await uow.jobs.enqueue(
                job_type="t", payload={}, run_at=NOW, idempotency_key=k, concurrency_key="domain:a"
            )
        got = await uow.jobs.claim_due(now=NOW, lease_owner="w", lease_seconds=60, limit=5)
        assert len(got) == 1
        assert await uow.jobs.claim_due(now=NOW, lease_owner="w2", lease_seconds=60, limit=5) == []


async def test_tables_metadata_matches_live_schema(engine: AsyncEngine) -> None:  # (i)
    async with engine.connect() as conn:
        live = await conn.run_sync(
            lambda c: {
                name: {col["name"]: col["nullable"] for col in sa.inspect(c).get_columns(name)}
                for name in sa.inspect(c).get_table_names()
                if name != "alembic_version"
            }
        )
    declared = {
        name: {c.name: c.nullable and not c.primary_key for c in tbl.columns}
        for name, tbl in tables.METADATA.tables.items()
    }
    assert set(live) == set(declared), set(live) ^ set(declared)
    for name in live:
        assert set(live[name]) == set(declared[name]), (
            f"{name}: {set(live[name]) ^ set(declared[name])}"
        )
        for col, nullable in live[name].items():
            assert declared[name][col] == nullable, f"{name}.{col} nullability drifted"


async def test_approvals_roundtrip(factory: SqlUnitOfWorkFactory) -> None:
    sid = uuid4()
    async with factory() as uow:
        ap = await uow.approvals.create(
            kind="message_send", subject_type="msg", subject_id=sid, requested_by="agent"
        )
        assert ap.status is ApprovalStatus.PENDING
        assert [a.id for a in (await uow.approvals.list_pending()).items] == [ap.id]
        done = await uow.approvals.decide(
            ap.id, status=ApprovalStatus.APPROVED, decided_by="op", reason="ok"
        )
        assert done.status is ApprovalStatus.APPROVED and done.decided_by == "op"
        with pytest.raises(ApprovalAlreadyDecided):
            await uow.approvals.decide(
                ap.id, status=ApprovalStatus.REJECTED, decided_by="op", reason=None
            )
        with pytest.raises(LookupError):
            await uow.approvals.decide(
                uuid4(), status=ApprovalStatus.APPROVED, decided_by="op", reason=None
            )
        assert (await uow.approvals.list_pending()).items == ()
        assert (
            await uow.approvals.latest_for_subject(
                subject_type="msg", subject_id=sid, kind="message_send"
            )
            == done
        )
        assert await uow.approvals.get(uuid4()) is None
        with pytest.raises(ValueError):
            await uow.approvals.create(
                kind="bogus", subject_type="m", subject_id=sid, requested_by="a"
            )


async def test_settings_versioning(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        assert await uow.settings.get("kill_switch") is None
        assert await uow.settings.set("kill_switch", {"on": False}, expected_version=0) == 1
        with pytest.raises(OptimisticLockError):
            await uow.settings.set("kill_switch", {"on": True}, expected_version=0)
        assert await uow.settings.set("kill_switch", {"on": True}, expected_version=1) == 2
        with pytest.raises(OptimisticLockError):
            await uow.settings.set("kill_switch", {"on": False}, expected_version=1)  # stale
        got = await uow.settings.get("kill_switch")
        assert got == {"key": "kill_switch", "value": {"on": True}, "version": 2}
        assert await uow.settings.set("kill_switch", {"on": False}, expected_version=None) == 3


async def test_agent_runs_roundtrip(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        rid = await uow.agent_runs.start(
            agent="qualification",
            task_type="q",
            campaign_id=c.id,
            input_refs={"p": 1},
            model="m",
            provider="mock",
            correlation_id="cor",
        )
        await uow.agent_runs.record_tool_call(
            agent_run_id=rid,
            tool_name="metrics.get",
            args_redacted={"d": "a.com"},
            result_summary=None,
            status="ok",
        )
        await uow.agent_runs.finish(
            rid, structured_result={"ok": True}, confidence=0.5, latency_ms=12, tokens_in=3
        )
        got = await uow.agent_runs.get(rid)
        assert (
            got is not None
            and got["structured_result"] == {"ok": True}
            and got["finished_at"] is not None
        )
        assert got["tokens_in"] == 3 and got["fallback_used"] is False
        page = await uow.agent_runs.list(campaign_id=c.id, agent="qualification")
        assert [r["id"] for r in page.items] == [rid]
        assert (await uow.agent_runs.list(agent="other")).items == ()
        assert await uow.agent_runs.get(uuid4()) is None
        with pytest.raises(LookupError):
            await uow.agent_runs.finish(
                uuid4(), structured_result=None, confidence=None, latency_ms=None
            )
