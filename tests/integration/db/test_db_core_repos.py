"""Deep coverage: audit, policies, campaigns, domains, metrics, prospects, urls (I1-critical)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from kb_contracts.enums import CampaignStatus, ProspectStatus
from kb_core.db.protocols import OptimisticLockError, UnitOfWork, UnitOfWorkFactory
from kb_core.db.seed import policy_content_hash, seed_policy_v1
from kb_core.db.uow import SqlUnitOfWork, SqlUnitOfWorkFactory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


def test_uow_and_factory_satisfy_frozen_protocols(factory: SqlUnitOfWorkFactory) -> None:
    # Annotated bindings: mypy --strict verifies the structural match, not just runtime.
    f: UnitOfWorkFactory = factory
    u: UnitOfWork = f()
    assert isinstance(u, SqlUnitOfWork)


async def test_audit_trigger_rejects_update_and_delete(
    factory: SqlUnitOfWorkFactory,
) -> None:  # (a)
    async with factory() as uow:
        aid = await uow.audit.append(
            actor_type="system",
            actor_id="t",
            action="x",
            subject_type="s",
            subject_id=None,
            correlation_id="c",
        )
    for sql in (
        "UPDATE audit_events SET action='y' WHERE id=:i",
        "DELETE FROM audit_events WHERE id=:i",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with factory() as uow:
                await uow.session.execute(text(sql), {"i": aid})
    async with factory() as uow:
        rows = await uow.audit.list_for_subject("s", aid)  # different subject: empty, no error
        assert rows == []
        n = (await uow.session.execute(text("select action from audit_events"))).scalars().all()
        assert n == ["x"]


async def test_audit_history_ordered_and_scoped(factory: SqlUnitOfWorkFactory) -> None:
    sid = uuid4()
    async with factory() as uow:
        for a in ("one", "two", "three"):
            await uow.audit.append(
                actor_type="agent",
                actor_id="a",
                action=a,
                subject_type="prospect",
                subject_id=sid,
                correlation_id="c",
                new_state={"k": a},
            )
        await uow.audit.append(
            actor_type="agent",
            actor_id="a",
            action="other",
            subject_type="prospect",
            subject_id=uuid4(),
            correlation_id="c",
        )
        rows = await uow.audit.list_for_subject("prospect", sid)
    assert [r["action"] for r in rows] == ["one", "two", "three"]


async def test_seed_idempotent_and_policy_lookup(factory: SqlUnitOfWorkFactory) -> None:  # (h)
    async with factory() as uow:
        v1 = await seed_policy_v1(uow)
    async with factory() as uow:
        v2 = await seed_policy_v1(uow)
        count = (
            await uow.session.execute(text("select count(*) from policy_versions"))
        ).scalar_one()
        audits = (
            await uow.session.execute(
                text("select count(*) from audit_events where action='policy.version.seeded'")
            )
        ).scalar_one()
        glob = await uow.policies.active_global_version()
        got = await uow.policies.get_version(v1.id)
    assert v1.id == v2.id and count == 1 and audits == 1
    assert glob is not None and glob.id == v1.id and got == v1
    assert v1.body.min_da == 45.0 and v1.body.max_spam == 1.0
    assert v1.body.required_metrics == ("domain_authority", "spam_score")
    assert v1.body.on_missing_metric == "defer"
    assert v1.content_hash == policy_content_hash(v1.body)


async def test_policy_for_campaign_prefers_pin(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        v = await seed_policy_v1(uow)
        c = await uow.campaigns.create(name="c")
        assert (await uow.policies.active_version_for_campaign(c.id)) == v  # falls back to global
        pinned = await uow.campaigns.create(name="p", policy_version_id=v.id)
        assert (await uow.policies.active_version_for_campaign(pinned.id)) == v
        assert await uow.policies.get_version(uuid4()) is None


async def test_campaign_crud_and_pagination(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        made = [await uow.campaigns.create(name=f"c{i}", goals={"i": i}) for i in range(5)]
        got = await uow.campaigns.get(made[0].id)
        assert got == made[0] and got.status is CampaignStatus.DRAFT and got.version == 1
        assert await uow.campaigns.get(uuid4()) is None
        seen = []
        cur = None
        while True:
            page = await uow.campaigns.list(cursor=cur, limit=2)
            seen += [c.name for c in page.items]
            cur = page.next_cursor
            if cur is None:
                break
    assert seen == [f"c{i}" for i in range(5)]


async def test_campaign_set_status_bumps_version(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        c2 = await uow.campaigns.set_status(c.id, "ACTIVE", expected_version=1)
        assert c2.status is CampaignStatus.ACTIVE and c2.version == 2
        with pytest.raises(ValueError):
            await uow.campaigns.set_status(c.id, "BOGUS", expected_version=2)
        with pytest.raises(LookupError):
            await uow.campaigns.set_status(uuid4(), "ACTIVE", expected_version=1)


async def test_optimistic_lock_conflict(factory: SqlUnitOfWorkFactory) -> None:  # (c)
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        d = await uow.domains.get_or_create("a.com")
        p = await uow.prospects.create(campaign_id=c.id, domain_id=d.id)
    # Two competing writers both read version 1; the first wins, the stale one must lose.
    async with factory() as w2:
        async with factory() as w1:
            await w1.campaigns.set_status(c.id, "ACTIVE", expected_version=1)
            await w1.prospects.set_status(p.id, ProspectStatus.ELIGIBLE, expected_version=1)
            loser = asyncio.create_task(w2.campaigns.set_status(c.id, "PAUSED", expected_version=1))
            await asyncio.sleep(0.2)
            assert not loser.done()  # row-locked behind w1's uncommitted UPDATE
        # w1 committed on exit; the stale writer now fails instead of overwriting.
        with pytest.raises(OptimisticLockError) as ei:
            await loser
        assert ei.value.expected == 1 and ei.value.row_id == c.id
        with pytest.raises(OptimisticLockError):
            await w2.prospects.set_status(p.id, ProspectStatus.REJECTED, expected_version=1)
    async with factory() as uow:
        assert (await uow.campaigns.get(c.id)).status is CampaignStatus.ACTIVE  # type: ignore[union-attr]
        got = await uow.prospects.get(p.id)
        assert got is not None and got.status is ProspectStatus.ELIGIBLE and got.version == 2


async def test_domain_get_or_create_idempotent_and_normalised(
    factory: SqlUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        a = await uow.domains.get_or_create("Example.COM ")
        b = await uow.domains.get_or_create("example.com")
        assert a.id == b.id and a.registered_domain == "example.com"
        assert await uow.domains.get(a.id) == a
        assert await uow.domains.get(uuid4()) is None
        with pytest.raises(ValueError):
            await uow.domains.get_or_create("  ")


async def test_metric_three_states(factory: SqlUnitOfWorkFactory) -> None:  # (d)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with factory() as uow:
        d = await uow.domains.get_or_create("m.com")
        await uow.domain_metrics.record(
            domain_id=d.id,
            provider="p",
            metric="domain_authority",
            value=51.5,
            source="fixture",
            fetched_at=t0,
        )
        await uow.domain_metrics.record(
            domain_id=d.id,
            provider="p",
            metric="spam_score",
            value=None,
            source="fixture",
            fetched_at=t0,
        )
        got = await uow.domain_metrics.latest(d.id, ["domain_authority", "spam_score", "traffic"])
    assert got["domain_authority"] == 51.5  # float
    assert "spam_score" in got and got["spam_score"] is None  # fetched, unavailable
    assert "traffic" not in got  # never fetched: ABSENT, not None
    assert set(got) == {"domain_authority", "spam_score"}


async def test_metric_latest_wins_even_when_newer_is_unavailable(
    factory: SqlUnitOfWorkFactory,
) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with factory() as uow:
        d = await uow.domains.get_or_create("n.com")
        for val, at in ((40.0, t0), (60.0, t0 + timedelta(days=1)), (None, t0 + timedelta(days=2))):
            await uow.domain_metrics.record(
                domain_id=d.id, provider="p", metric="da", value=val, source="s", fetched_at=at
            )
        assert (await uow.domain_metrics.latest(d.id, ["da"])) == {"da": None}
        await uow.domain_metrics.record(
            domain_id=d.id,
            provider="p",
            metric="da",
            value=70.0,
            source="s",
            fetched_at=t0 + timedelta(days=3),
        )
        assert (await uow.domain_metrics.latest(d.id, ["da"])) == {"da": 70.0}
        assert await uow.domain_metrics.latest(d.id, []) == {}


async def test_metric_rejects_naive_time_and_nonfinite(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        d = await uow.domains.get_or_create("o.com")
        with pytest.raises(ValueError, match="timezone-aware"):
            await uow.domain_metrics.record(
                domain_id=d.id,
                provider="p",
                metric="da",
                value=1.0,
                source="s",
                fetched_at=datetime(2026, 1, 1),
            )
        with pytest.raises(ValueError):
            await uow.domain_metrics.record(
                domain_id=d.id,
                provider="p",
                metric="da",
                value=float("nan"),
                source="s",
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            )


async def test_domain_metrics_is_append_only_in_db(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        d = await uow.domains.get_or_create("q.com")
        m = await uow.domain_metrics.record(
            domain_id=d.id,
            provider="p",
            metric="da",
            value=1.0,
            source="s",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(DBAPIError, match="append-only"):
        async with factory() as uow:
            await uow.session.execute(
                text("update domain_metrics set value=99 where id=:i"), {"i": m.id}
            )


async def test_prospect_create_dedupes_and_counts(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        other = await uow.campaigns.create(name="o")
        d1, d2 = await uow.domains.get_or_create("a.com"), await uow.domains.get_or_create("b.com")
        p1 = await uow.prospects.create(campaign_id=c.id, domain_id=d1.id, discovered_via="serp")
        again = await uow.prospects.create(campaign_id=c.id, domain_id=d1.id)
        assert again.id == p1.id and again.discovered_via == "serp"
        p2 = await uow.prospects.create(campaign_id=c.id, domain_id=d2.id)
        await uow.prospects.create(campaign_id=other.id, domain_id=d1.id)  # other campaign OK
        await uow.prospects.set_status(p2.id, ProspectStatus.DEFERRED, expected_version=1)
        counts = await uow.prospects.count_by_status(c.id)
        assert counts[ProspectStatus.DISCOVERED] == 1 and counts[ProspectStatus.DEFERRED] == 1
        assert counts[ProspectStatus.WON] == 0 and set(counts) == set(ProspectStatus)
        assert (await uow.prospects.get_by_domain(c.id, d2.id)) is not None
        assert (await uow.prospects.get_by_domain(other.id, d2.id)) is None
        page = await uow.prospects.list(campaign_id=c.id, status=ProspectStatus.DEFERRED)
        assert [p.id for p in page.items] == [p2.id] and page.next_cursor is None
        assert await uow.prospects.get(uuid4()) is None


async def test_prospect_urls_upsert_idempotent(factory: SqlUnitOfWorkFactory) -> None:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        d = await uow.domains.get_or_create("a.com")
        p = await uow.prospects.create(campaign_id=c.id, domain_id=d.id)
        u1 = await uow.prospect_urls.upsert(
            prospect_id=p.id,
            url="https://a.com/x?utm=1",
            canonical_url="https://a.com/x",
            url_hash="h1",
            page_type="guest",
            provenance={"q": "k"},
        )
        u2 = await uow.prospect_urls.upsert(
            prospect_id=p.id, url="https://a.com/x", canonical_url="https://a.com/x", url_hash="h1"
        )
        await uow.prospect_urls.upsert(
            prospect_id=p.id, url="https://a.com/y", canonical_url="https://a.com/y", url_hash="h2"
        )
        listed = await uow.prospect_urls.list_for_prospect(p.id)
    assert u1.id == u2.id and u2.provenance == {"q": "k"} and u2.page_type == "guest"
    assert [u.url_hash for u in listed] == ["h1", "h2"]


async def test_uow_rollback_leaves_no_audit_and_no_status_change(
    factory: SqlUnitOfWorkFactory,
) -> None:  # (e)
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")

    class Boom(Exception): ...

    with pytest.raises(Boom):
        async with factory() as uow:
            await uow.campaigns.set_status(c.id, "ACTIVE", expected_version=1)
            await uow.audit.append(
                actor_type="system",
                actor_id="t",
                action="campaign.activated",
                subject_type="campaign",
                subject_id=c.id,
                correlation_id="c",
            )
            await uow.flush()
            # visible inside the transaction...
            assert len(await uow.audit.list_for_subject("campaign", c.id)) == 1
            raise Boom
    async with factory() as uow:  # ...but gone after rollback
        assert await uow.audit.list_for_subject("campaign", c.id) == []
        got = await uow.campaigns.get(c.id)
        assert got is not None and got.status is CampaignStatus.DRAFT and got.version == 1


async def test_uow_commit_makes_transition_and_audit_visible_together(
    factory: SqlUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        await uow.campaigns.set_status(c.id, "ACTIVE", expected_version=1)
        await uow.audit.append(
            actor_type="system",
            actor_id="t",
            action="a",
            subject_type="campaign",
            subject_id=c.id,
            correlation_id="c",
        )
    async with factory() as other:
        got = await other.campaigns.get(c.id)
        assert got is not None and got.status is CampaignStatus.ACTIVE
        assert len(await other.audit.list_for_subject("campaign", c.id)) == 1


async def test_uow_is_single_use(factory: SqlUnitOfWorkFactory) -> None:
    uow = factory()
    async with uow:
        pass
    with pytest.raises(RuntimeError):
        async with uow:
            pass
