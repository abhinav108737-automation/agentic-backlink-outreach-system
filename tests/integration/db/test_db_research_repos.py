"""Deep coverage: qualifications, rankings, evidence, dossiers, contacts, suppression."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from kb_contracts.enums import QualificationOutcome, SuppressionReason, SuppressionScope
from kb_core.db.seed import seed_policy_v1
from kb_core.db.uow import SqlUnitOfWorkFactory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.db


async def _prospect(factory: SqlUnitOfWorkFactory) -> tuple[UUID, UUID, UUID, UUID]:
    """Returns (prospect_id, domain_id, policy_version_id, campaign_id), committed."""
    async with factory() as uow:
        pv = await seed_policy_v1(uow)
        c = await uow.campaigns.create(name="c")
        d = await uow.domains.get_or_create("site.com")
        p = await uow.prospects.create(campaign_id=c.id, domain_id=d.id)
    return p.id, d.id, pv.id, c.id


async def test_qualification_deferred_roundtrip_and_latest(factory: SqlUnitOfWorkFactory) -> None:
    pid, _, pv, _ = await _prospect(factory)
    async with factory() as uow:
        first = await uow.qualifications.record(
            prospect_id=pid,
            policy_version_id=pv,
            outcome="DEFERRED",
            reason_codes=["missing_metric"],
            metric_snapshot={"domain_authority": 60.0, "spam_score": None},
            missing_metrics=["spam_score"],
        )
        assert first.outcome is QualificationOutcome.DEFERRED
        assert first.reason_codes == ("missing_metric",)
        assert first.metric_snapshot == {"domain_authority": 60.0, "spam_score": None}
        assert first.missing_metrics == ("spam_score",)
        second = await uow.qualifications.record(
            prospect_id=pid,
            policy_version_id=pv,
            outcome="ELIGIBLE",
            reason_codes=["ok"],
            metric_snapshot={"domain_authority": 60.0, "spam_score": 0.2},
            missing_metrics=[],
        )
        latest = await uow.qualifications.latest(pid)
        assert latest == second and latest.id != first.id
        assert await uow.qualifications.latest(uuid4()) is None


async def test_qualification_guards(factory: SqlUnitOfWorkFactory) -> None:
    pid, _, pv, _ = await _prospect(factory)
    async with factory() as uow:
        with pytest.raises(ValueError, match="missing_metrics"):
            await uow.qualifications.record(
                prospect_id=pid,
                policy_version_id=pv,
                outcome="ELIGIBLE",
                reason_codes=[],
                metric_snapshot={},
                missing_metrics=["spam_score"],
            )
        with pytest.raises(ValueError):
            await uow.qualifications.record(
                prospect_id=pid,
                policy_version_id=pv,
                outcome="MAYBE",
                reason_codes=[],
                metric_snapshot={},
                missing_metrics=[],
            )


async def test_ranking_latest(factory: SqlUnitOfWorkFactory) -> None:
    pid, _, pv, _ = await _prospect(factory)
    async with factory() as uow:
        await uow.rankings.record(
            prospect_id=pid, policy_version_id=pv, score=0.4, components={"a": 0.4}, explanation="x"
        )
        r2 = await uow.rankings.record(
            prospect_id=pid,
            policy_version_id=pv,
            score=0.9,
            components={"a": 0.5, "b": 0.4},
            explanation="y",
        )
        got = await uow.rankings.latest(pid)
        assert got == r2 and got.components == {"a": 0.5, "b": 0.4}
        with pytest.raises(ValueError):
            await uow.rankings.record(
                prospect_id=pid,
                policy_version_id=pv,
                score=float("inf"),
                components={},
                explanation="",
            )
        assert await uow.rankings.latest(uuid4()) is None


async def test_evidence_roundtrip_for_claim_verification(factory: SqlUnitOfWorkFactory) -> None:
    pid, _, _, _ = await _prospect(factory)
    at = datetime(2026, 2, 1, tzinfo=UTC)
    items = [
        {
            "source_url": "https://site.com/a",
            "excerpt": "e1",
            "content_hash": "h1",
            "kind": "about",
            "extracted_at": at,
        },
        {
            "source_url": "https://site.com/b",
            "excerpt": "e2",
            "content_hash": "h2",
            "kind": "post",
            "extracted_at": at,
            "extractor": "llm",
        },
    ]
    async with factory() as uow:
        assert await uow.evidence.record_many(pid, []) == ()
        recs = await uow.evidence.record_many(pid, items)
        assert [r.excerpt for r in recs] == ["e1", "e2"]
        assert [r.extractor for r in recs] == ["deterministic", "llm"]
        got = await uow.evidence.get_many([recs[1].id, recs[0].id, uuid4()])
        assert {g.id for g in got} == {recs[0].id, recs[1].id}  # unknown id simply absent
        assert await uow.evidence.get_many([]) == ()
        assert [e.id for e in await uow.evidence.list_for_prospect(pid)] == [r.id for r in recs]
        with pytest.raises(ValueError, match="missing keys"):
            await uow.evidence.record_many(pid, [{"source_url": "x"}])
        with pytest.raises(ValueError, match="timezone-aware"):
            await uow.evidence.record_many(
                pid, [{**items[0], "extracted_at": datetime(2026, 1, 1)}]
            )


async def test_dossier_version_allocation(factory: SqlUnitOfWorkFactory) -> None:
    pid, _, _, _ = await _prospect(factory)
    async with factory() as uow:
        d1 = await uow.dossiers.create(
            prospect_id=pid, summary="s1", topics=["a"], editorial_signals={"x": 1}
        )
        d2 = await uow.dossiers.create(
            prospect_id=pid, summary="s2", topics=[], editorial_signals={}
        )
        assert (d1.version_no, d2.version_no) == (1, 2)
        assert d1.topics == ("a",) and d1.editorial_signals == {"x": 1}
        assert await uow.dossiers.latest(pid) == d2
        with pytest.raises(LookupError):
            await uow.dossiers.create(
                prospect_id=uuid4(), summary="", topics=[], editorial_signals={}
            )


async def test_dossier_concurrent_creates_get_distinct_versions(
    factory: SqlUnitOfWorkFactory,
) -> None:
    pid, _, _, _ = await _prospect(factory)

    async def make() -> int:
        async with factory() as uow:
            return (
                await uow.dossiers.create(
                    prospect_id=pid, summary="s", topics=[], editorial_signals={}
                )
            ).version_no

    assert sorted(await asyncio.gather(make(), make(), make())) == [1, 2, 3]


async def test_contacts_and_channels_idempotent(factory: SqlUnitOfWorkFactory) -> None:
    _, did, _, _ = await _prospect(factory)
    async with factory() as uow:
        c = await uow.contacts.create(
            domain_id=did,
            name="Ann",
            role="editor",
            contact_type="editor",
            confidence=0.8,
            provenance={"src": "about"},
        )
        ch1 = await uow.contacts.add_channel(
            contact_id=c.id, channel_type="email", value_normalized="ann@site.com", confidence=0.7
        )
        ch2 = await uow.contacts.add_channel(
            contact_id=c.id, channel_type="email", value_normalized="ann@site.com", confidence=0.9
        )
        assert ch1.id == ch2.id
        await uow.contacts.add_channel(
            contact_id=c.id,
            channel_type="form",
            value_normalized="https://site.com/contact",
            confidence=0.5,
        )
        assert [x.id for x in await uow.contacts.list_for_domain(did)] == [c.id]
        assert len(await uow.contacts.list_channels(did)) == 2
        assert await uow.contacts.list_channels(uuid4()) == []
        with pytest.raises(ValueError, match="lower-cased"):
            await uow.contacts.add_channel(
                contact_id=c.id,
                channel_type="email",
                value_normalized="Ann@Site.com",
                confidence=0.5,
            )


async def test_suppression_guard_and_supersede(factory: SqlUnitOfWorkFactory) -> None:  # (b)
    async with factory() as uow:
        e = await uow.suppressions.add(
            scope=SuppressionScope.EMAIL, value_normalized="  Bob@Site.COM ", reason="opt_out"
        )
        assert e.value_normalized == "bob@site.com" and e.reason is SuppressionReason.OPT_OUT
        dup = await uow.suppressions.add(
            scope=SuppressionScope.EMAIL, value_normalized="bob@site.com", reason="manual"
        )
        assert dup.id == e.id  # active duplicate returns the existing entry
        assert await uow.suppressions.is_suppressed(
            scope=SuppressionScope.EMAIL, value_normalized="BOB@site.com"
        )
    with pytest.raises(DBAPIError, match="never deleted"):
        async with factory() as uow:
            await uow.session.execute(
                text("delete from suppression_entries where id=:i"), {"i": e.id}
            )
    with pytest.raises(DBAPIError, match="permits updating only"):
        async with factory() as uow:
            await uow.session.execute(
                text("update suppression_entries set reason='manual' where id=:i"), {"i": e.id}
            )
    async with factory() as uow:
        done = await uow.suppressions.supersede(e.id, superseded_by="operator")
        assert done.superseded_at is not None
        assert not await uow.suppressions.is_suppressed(
            scope=SuppressionScope.EMAIL, value_normalized="bob@site.com"
        )
        assert (
            await uow.suppressions.supersede(e.id, superseded_by="x")
        ).superseded_at == done.superseded_at
        with pytest.raises(LookupError):
            await uow.suppressions.supersede(uuid4(), superseded_by="x")


async def test_suppression_check_rejects_unnormalised_value(factory: SqlUnitOfWorkFactory) -> None:
    with pytest.raises(IntegrityError, match="value_normalized"):
        async with factory() as uow:
            await uow.session.execute(
                text(
                    "insert into suppression_entries (scope, value_normalized, reason) "
                    "values ('email', 'Bob@Site.com', 'manual')"
                )
            )


async def test_suppression_scoping_and_matching(factory: SqlUnitOfWorkFactory) -> None:
    _, _, _, cid = await _prospect(factory)
    async with factory() as uow:
        await uow.suppressions.add(
            scope=SuppressionScope.DOMAIN, value_normalized="bad.com", reason="legal"
        )
        await uow.suppressions.add(
            scope=SuppressionScope.EMAIL,
            value_normalized="x@ok.com",
            reason="bounce",
            campaign_id=cid,
        )
        s = uow.suppressions
        assert await s.is_suppressed(
            scope=SuppressionScope.DOMAIN, value_normalized="bad.com"
        )  # global
        assert await s.is_suppressed(
            scope=SuppressionScope.DOMAIN, value_normalized="bad.com", campaign_id=cid
        )
        assert not await s.is_suppressed(
            scope=SuppressionScope.EMAIL, value_normalized="x@ok.com"
        )  # campaign-only
        assert await s.is_suppressed(
            scope=SuppressionScope.EMAIL, value_normalized="x@ok.com", campaign_id=cid
        )
        assert not await s.is_suppressed(
            scope=SuppressionScope.EMAIL, value_normalized="x@ok.com", campaign_id=uuid4()
        )
        hits = await s.matching(
            values={SuppressionScope.DOMAIN: "bad.com", SuppressionScope.EMAIL: "x@ok.com"},
            campaign_id=cid,
        )
        assert len(hits) == 2
        assert await s.matching(values={}) == ()
