"""Backlink verification driving the state machine, over PostgreSQL. Fixture pages only."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from flowkit import CORR, World, job
from kb_contracts.domain import Backlink
from kb_contracts.enums import BacklinkStatus
from kb_contracts.providers import (
    FetchedPage,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from kb_core.verification.evaluate import count_active
from kb_core.verification.handlers import make_verify_handler
from kb_core.verification.service import VerificationService
from kb_providers.crawler.mock import MockCrawlerProvider
from sqlalchemy.exc import DBAPIError

B = BacklinkStatus
HOST = "https://publisher-verification-lab.net"
TARGET = "https://kenresearch.example.test/reports"
ANCHOR = "healthcare market research reports"
LINK = f'<a href="{TARGET}">{ANCHOR}</a>'


def html(link: str = LINK) -> str:
    return f"<html><body><article><p>Industry note: {link} for details.</p></article></body></html>"


class Script:
    """Offline crawler that serves a scripted sequence of pages / errors, then repeats the last."""

    name, is_paid = "scripted_crawler", False

    def __init__(self, *steps: FetchedPage | type[ProviderError]) -> None:
        self.steps, self.calls = list(steps), 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, status="ok")

    async def fetch(self, url: str) -> FetchedPage:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if isinstance(step, type):
            raise step("scripted")
        return step.model_copy(update={"url": url, "final_url": url})


def page(body: str = "", status: int = 200, robots: bool = True) -> FetchedPage:
    from datetime import UTC, datetime

    return FetchedPage(
        url="x",
        final_url="x",
        status=status,
        html=body,
        fetched_at=datetime(2026, 3, 2, tzinfo=UTC),
        robots_allowed=robots,
    )


GOOD = page(html())
NOFOLLOW = page(html(f'<a href="{TARGET}" rel="nofollow">{ANCHOR}</a>'))
GONE = page(html("no link here"))


async def make_backlink(world: World, path: str = "/good-article") -> Backlink:
    async with world.factory() as uow:
        return await uow.backlinks.create(
            opportunity_id=None,
            source_url=f"{HOST}{path}",
            target_url=TARGET,
            expected_anchor=ANCHOR,
        )


def svc(world: World, crawler, **kw) -> VerificationService:
    return VerificationService(world.factory, crawler, world.transitions, world.clock, **kw)


async def current(world: World, b: Backlink) -> Backlink:
    async with world.factory() as uow:
        fresh = await uow.backlinks.get(b.id)
        assert fresh is not None
        return fresh


async def history(world: World, b: Backlink) -> list[tuple[str, str]]:
    async with world.factory() as uow:
        rows = await uow.audit.list_for_subject("backlink", b.id)
    return [(r["old_state"]["status"], r["new_state"]["status"]) for r in rows]


# ============================================================ each outcome from fixture pages
async def test_expected_to_found_to_verified_on_the_good_page(world: World) -> None:
    b = await make_backlink(world, "/good-article")
    r = await svc(world, MockCrawlerProvider()).verify(b.id, correlation_id=CORR)
    assert (r.outcome.value, r.status_before, r.backlink.status) == (
        "VERIFIED",
        B.EXPECTED,
        B.VERIFIED,
    )
    assert r.active is True and r.consecutive_misses == 0
    assert sorted(await history(world, b)) == sorted([("EXPECTED", "FOUND"), ("FOUND", "VERIFIED")])
    (v,) = await world.sql("select * from backlink_verifications")
    assert v["result"] == "VERIFIED" and v["link_found"] is True and v["http_status"] == 200
    assert v["verifier_version"] == "backlink-verifier-1" and v["rel_attrs"] == []
    assert v["anchor_text"] == ANCHOR and v["canonical_url"] == f"{HOST}/good-article"
    assert ANCHOR in v["context_excerpt"]
    assert (await current(world, b)).consecutive_misses == 0


async def test_changed_on_nofollow_and_missing_on_removed_link(world: World) -> None:
    nofollow = await make_backlink(world, "/nofollow-article")
    r = await svc(world, MockCrawlerProvider()).verify(nofollow.id, correlation_id=CORR)
    assert r.backlink.status is B.CHANGED and not r.active and "rel_nofollow_sponsored" in r.reasons
    (v,) = await world.sql(
        "select rel_attrs from backlink_verifications where backlink_id = :b", b=nofollow.id
    )
    assert v["rel_attrs"] == ["nofollow", "sponsored"]

    removed = await make_backlink(world, "/link-removed")
    r = await svc(world, MockCrawlerProvider()).verify(removed.id, correlation_id=CORR)
    assert (r.backlink.status, r.consecutive_misses, r.active) == (B.MISSING, 1, False)
    (v,) = await world.sql(
        "select link_found, result from backlink_verifications where backlink_id = :b", b=removed.id
    )
    assert (v["link_found"], v["result"]) == (False, "MISSING")

    gone = await make_backlink(world, "/not-a-page")
    r = await svc(world, MockCrawlerProvider()).verify(gone.id, correlation_id=CORR)
    assert r.backlink.status is B.MISSING and r.reasons == ("http_404",)


async def test_comment_injection_on_the_published_page_is_not_obeyed(world: World) -> None:
    b = await make_backlink(world, "/injected-article")
    r = await svc(world, MockCrawlerProvider()).verify(b.id, correlation_id=CORR)
    assert r.backlink.status is B.VERIFIED  # verified because the link is really there


# ============================================================ MISSING -> LOST and recovery
async def test_full_lifecycle_lost_after_three_consecutive_misses_and_recovery(
    world: World,
) -> None:
    b = await make_backlink(world)
    crawler = Script(GOOD, GONE, GONE, GONE, GOOD, NOFOLLOW, GOOD)
    s = svc(world, crawler)
    seen: list[tuple[B, int, bool]] = []
    for _ in range(7):
        r = await s.verify(b.id, correlation_id=CORR)
        seen.append((r.backlink.status, r.consecutive_misses, r.active))
        # the single definition of active: VERIFIED and nothing else
        assert r.active is (r.backlink.status is B.VERIFIED)
    assert seen == [
        (B.VERIFIED, 0, True),
        (B.MISSING, 1, False),
        (B.MISSING, 2, False),
        (B.LOST, 3, False),
        (B.VERIFIED, 0, True),
        (B.CHANGED, 0, False),
        (B.VERIFIED, 0, True),
    ]
    assert len(await world.sql("select 1 from backlink_verifications")) == 7  # one row per pass
    path = await history(world, b)
    assert ("MISSING", "LOST") in path and ("LOST", "FOUND") in path  # the audited reappearance
    assert ("FOUND", "VERIFIED") in path and ("VERIFIED", "CHANGED") in path
    (row,) = await world.sql(
        "select consecutive_misses, first_seen_at, last_seen_at from backlinks"
    )
    assert row["consecutive_misses"] == 0 and row["first_seen_at"] <= row["last_seen_at"]


async def test_missing_and_lost_are_never_counted_as_active(world: World) -> None:
    verified = await make_backlink(world, "/good-article")
    missing = await make_backlink(world, "/link-removed")
    lost = await make_backlink(world, "/link-removed")
    crawler = MockCrawlerProvider()
    await svc(world, crawler).verify(verified.id, correlation_id=CORR)
    await svc(world, crawler).verify(missing.id, correlation_id=CORR)
    for _ in range(3):
        await svc(world, crawler).verify(lost.id, correlation_id=CORR)
    assert (await current(world, missing)).status is B.MISSING
    assert (await current(world, lost)).status is B.LOST
    async with world.factory() as uow:
        everything = list((await uow.backlinks.list(limit=50)).items)
    assert len(everything) == 3 and count_active(everything) == 1
    assert [b.id for b in everything if b.status is B.VERIFIED] == [verified.id]
    (n,) = await world.sql("select count(*) n from backlinks where status = 'VERIFIED'")
    assert n["n"] == 1


async def test_a_sighting_resets_the_consecutive_miss_count(world: World) -> None:
    b = await make_backlink(world)
    s = svc(world, Script(GOOD, GONE, GOOD, GONE, GONE))
    for _ in range(5):
        r = await s.verify(b.id, correlation_id=CORR)
    assert (r.backlink.status, r.consecutive_misses) == (B.MISSING, 2)  # not LOST: misses reset


async def test_the_lost_threshold_is_configurable(world: World) -> None:
    b = await make_backlink(world)
    s = svc(world, Script(GOOD, GONE), lost_after_misses=1)
    await s.verify(b.id, correlation_id=CORR)
    r = await s.verify(b.id, correlation_id=CORR)
    assert r.backlink.status is B.LOST
    with pytest.raises(ValueError):
        svc(world, Script(GOOD), lost_after_misses=0)


async def test_inconclusive_checks_never_demote_a_link(world: World) -> None:
    b = await make_backlink(world)
    crawler = Script(
        GOOD, page(status=503), page(status=429), ProviderUnavailable, page(robots=False)
    )
    s = svc(world, crawler)
    await s.verify(b.id, correlation_id=CORR)
    for _ in range(4):
        r = await s.verify(b.id, correlation_id=CORR)
        assert r.outcome.value == "INCONCLUSIVE" and r.backlink.status is B.VERIFIED
        assert r.consecutive_misses == 0
    for _ in range(10):  # a host that stays unreachable is still not a miss
        r = await s.verify(b.id, correlation_id=CORR)
    assert r.backlink.status is B.VERIFIED
    results = await world.sql("select result from backlink_verifications")
    assert {x["result"] for x in results} == {"VERIFIED", "INCONCLUSIVE"}


async def test_expected_backlink_gets_a_grace_period_before_it_counts_as_missing(
    world: World,
) -> None:
    b = await make_backlink(world)
    s = svc(world, Script(GONE), expected_grace=timedelta(days=7))
    r = await s.verify(b.id, correlation_id=CORR)
    assert r.backlink.status is B.EXPECTED and r.consecutive_misses == 0
    assert len(await world.sql("select 1 from backlink_verifications")) == 1  # still recorded
    # created_at is stamped by the database clock; age the row relative to the injected clock
    await world.sql(
        "update backlinks set created_at = :t", t=world.clock.now() - timedelta(days=30)
    )
    r = await svc(world, Script(GONE), expected_grace=timedelta(days=7)).verify(
        b.id, correlation_id=CORR
    )
    assert r.backlink.status is B.MISSING


async def test_verification_rows_are_append_only(world: World) -> None:
    b = await make_backlink(world)
    await svc(world, Script(GOOD)).verify(b.id, correlation_id=CORR)
    with pytest.raises(DBAPIError, match="append-only"):
        await world.sql("update backlink_verifications set result = 'VERIFIED'")
    with pytest.raises(DBAPIError, match="append-only"):
        await world.sql("delete from backlink_verifications")


# ============================================================ job handler
def verify_job(b: Backlink, corr: str = CORR):
    return job("backlink.verify", {"backlink_id": str(b.id), "correlation_id": corr})


async def test_verify_handler_is_safe_to_redeliver_and_schedules_rechecks(world: World) -> None:
    b = await make_backlink(world)
    handler = make_verify_handler(
        world.factory, Script(GOOD), world.transitions, world.clock, recheck_every=timedelta(days=7)
    )
    await handler(verify_job(b))
    await handler(verify_job(b))  # redelivery appends a check but changes no state
    assert (await current(world, b)).status is B.VERIFIED
    assert len(await world.sql("select 1 from backlink_verifications")) == 2
    assert (
        await history(world, b) and len(await history(world, b)) == 2
    )  # only the first pass moved it
    rechecks = await world.sql(
        "select payload, subject_type, run_at from scheduled_jobs where job_type = 'backlink.verify'"
    )
    assert len(rechecks) == 1  # same correlation + same clock instant -> one idempotent job
    assert rechecks[0]["subject_type"] == "backlink"
    assert UUID(rechecks[0]["payload"]["backlink_id"]) == b.id
    assert rechecks[0]["run_at"] == world.clock.now() + timedelta(days=7)


async def test_verify_handler_stops_rechecking_once_lost(world: World) -> None:
    b = await make_backlink(world)
    handler = make_verify_handler(
        world.factory,
        Script(GOOD, GONE),
        world.transitions,
        world.clock,
        lost_after_misses=1,
        recheck_every=timedelta(days=1),
    )
    await handler(verify_job(b, "c1"))
    world.clock.advance(timedelta(days=1))
    await handler(verify_job(b, "c2"))
    assert (await current(world, b)).status is B.LOST
    assert (
        len(await world.sql("select 1 from scheduled_jobs where job_type = 'backlink.verify'")) == 1
    )
