"""Guarantees that only a real transaction can prove: atomic rollback and job queue bounds."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from kb_contracts.config import Settings
from kb_contracts.enums import CampaignStatus, JobStatus, ProspectStatus
from kb_core.audit.service import OPERATOR
from kb_core.db import tables as t
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.qualification.service import QualificationService
from kb_core.scheduling.clock import FakeClock
from kb_core.scheduling.queue import UowJobQueue
from kb_core.scheduling.worker import JobWorker
from kb_core.transitions.service import TransitionService

pytestmark = pytest.mark.db
T0 = datetime(2026, 5, 1, 9, tzinfo=UTC)


class FailingAudit:
    async def append(self, **kw):
        raise RuntimeError("audit write failed")


def failing_audit_factory(real):
    def make():
        uow = real()
        uow.audit = FailingAudit()
        return uow

    return make


async def test_transition_and_audit_roll_back_together_in_postgres(seeded):
    async with seeded() as uow:
        c = await uow.campaigns.create(name="atomic")
    svc = TransitionService(failing_audit_factory(seeded), FakeClock())
    with pytest.raises(RuntimeError):
        await svc.transition(
            c, CampaignStatus.ACTIVE, actor=OPERATOR, reason="r", correlation_id="x"
        )
    async with seeded() as uow:
        fresh = await uow.campaigns.get(c.id)
        assert (fresh.status, fresh.version) == (CampaignStatus.DRAFT, 1)
        assert await uow.audit.list_for_subject("campaign", c.id) == []

    ok = TransitionService(seeded, FakeClock())
    await ok.transition(c, CampaignStatus.ACTIVE, actor=OPERATOR, reason="r", correlation_id="x")
    async with seeded() as uow:
        assert (await uow.campaigns.get(c.id)).status is CampaignStatus.ACTIVE
        assert len(await uow.audit.list_for_subject("campaign", c.id)) == 1


async def test_qualification_decision_and_status_commit_together_or_not_at_all(seeded):
    async with seeded() as uow:
        c = await uow.campaigns.create(name="q")
        d = await uow.domains.get_or_create("atomic.com")
        p = await uow.prospects.create(campaign_id=c.id, domain_id=d.id)
    settings = Settings(_env_file=None)
    clock = FakeClock()
    failing = failing_audit_factory(seeded)
    q = QualificationService(
        failing, DeterministicPolicyEngine(failing, settings), TransitionService(failing, clock)
    )
    with pytest.raises(RuntimeError):
        await q.qualify(p.id, actor=OPERATOR, correlation_id="x")
    async with seeded() as uow:
        assert (await uow.prospects.get(p.id)).status is ProspectStatus.DISCOVERED
        assert await uow.qualifications.latest(p.id) is None  # decision rolled back too


async def test_db_defers_for_unavailable_and_never_fetched_separately(seeded):
    settings = Settings(_env_file=None)
    clock = FakeClock()
    q = QualificationService(
        seeded, DeterministicPolicyEngine(seeded, settings), TransitionService(seeded, clock)
    )
    async with seeded() as uow:
        c = await uow.campaigns.create(name="defer")
        pids = {}
        for name, metrics in {
            "state2.com": {"domain_authority": None, "spam_score": 0.1},
            "state3.com": {"spam_score": 0.1},
        }.items():
            d = await uow.domains.get_or_create(name)
            pids[name] = (await uow.prospects.create(campaign_id=c.id, domain_id=d.id)).id
            for m, v in metrics.items():
                await uow.domain_metrics.record(
                    domain_id=d.id, provider="t", metric=m, value=v, source="t", fetched_at=T0
                )
    r2 = await q.qualify(pids["state2.com"], actor=OPERATOR, correlation_id="c")
    r3 = await q.qualify(pids["state3.com"], actor=OPERATOR, correlation_id="c")
    for r in (r2, r3):
        assert r.prospect.status is ProspectStatus.DEFERRED
        assert r.decision.missing_metrics == ("domain_authority",)
    assert r2.decision.metric_snapshot == {"domain_authority": None, "spam_score": 0.1}
    assert r3.decision.metric_snapshot == {"spam_score": 0.1}
    assert "METRIC_UNAVAILABLE:domain_authority" in r2.decision.reason_codes
    assert "METRIC_NEVER_FETCHED:domain_authority" in r3.decision.reason_codes


async def test_real_job_queue_lease_retry_and_dead_letter_bounds_with_fake_clock(seeded):
    # Backoff is stamped on the DB clock, so the fake clock must start at real 'now'.
    start = datetime.now(UTC)
    clock = FakeClock(start)
    calls = []

    async def boom(job):
        calls.append(job.attempts)
        raise RuntimeError("x")

    worker = JobWorker(seeded, clock, {"t": boom}, lease_seconds=60)
    async with seeded() as uow:
        await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=start, idempotency_key="k", max_attempts=3
        )
    q = UowJobQueue(seeded)
    assert await q.enqueue("t", {}, start, "k") == await q.enqueue("t", {}, start, "k")

    async def row():
        async with seeded() as uow:
            r = (
                (
                    await uow.session.execute(
                        sa.select(t.scheduled_jobs).where(t.scheduled_jobs.c.idempotency_key == "k")
                    )
                )
                .mappings()
                .one()
            )
            return dict(r)

    assert (await worker.drain_once()).failed == 1
    r = await row()
    assert (r["status"], r["attempts"]) == ("RETRY_WAIT", 1)
    # Backoff is stamped on the DB clock, which may drift from the host's: anchor on run_at.
    clock = FakeClock(r["run_at"] - timedelta(seconds=1))
    worker = JobWorker(seeded, clock, {"t": boom}, lease_seconds=60)
    assert (await worker.drain_once()).claimed == 0  # backoff has not elapsed
    for attempts, status in ((2, "RETRY_WAIT"), (3, "DEAD")):
        clock.advance(timedelta(hours=2))  # past the one-hour backoff cap
        await worker.drain_once()
        r = await row()
        assert (r["attempts"], r["status"]) == (attempts, status)
    clock.advance(timedelta(days=30))
    assert (await worker.drain_once()).claimed == 0 and len(calls) == 3  # dead never reruns


async def test_real_lease_expiry_goes_dead_when_attempts_are_spent(seeded):
    async with seeded() as uow:
        await uow.jobs.enqueue(
            job_type="t", payload={}, run_at=T0, idempotency_key="l", max_attempts=1
        )
        got = await uow.jobs.claim_due(now=T0, lease_owner="w1", lease_seconds=60)
        assert len(got) == 1
    async with seeded() as uow:
        assert await uow.jobs.reclaim_expired(now=T0 + timedelta(seconds=59)) == 0
    async with seeded() as uow:
        assert await uow.jobs.reclaim_expired(now=T0 + timedelta(seconds=61)) == 1
    async with seeded() as uow:
        status = (
            await uow.session.execute(
                sa.select(t.scheduled_jobs.c.status).where(
                    t.scheduled_jobs.c.idempotency_key == "l"
                )
            )
        ).scalar_one()
    assert status == JobStatus.DEAD.value
