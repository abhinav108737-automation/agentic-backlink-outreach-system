"""Pure backlink checks against the fixture publisher pages (no database)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from kb_contracts.domain import Backlink
from kb_contracts.enums import BacklinkStatus
from kb_contracts.providers import FetchedPage
from kb_core.verification.evaluate import (
    ACTIVE_BACKLINK_STATUSES,
    Outcome,
    count_active,
    evaluate,
    is_active,
)
from kb_core.verification.page import analyse_page
from kb_providers.crawler.mock import MockCrawlerProvider

HOST = "https://publisher-verification-lab.net"
TARGET = "https://kenresearch.example.test/reports"
ANCHOR = "healthcare market research reports"
NOW = datetime(2026, 3, 1, tzinfo=UTC)


def backlink(
    path: str,
    *,
    anchor: str | None = ANCHOR,
    status: BacklinkStatus = BacklinkStatus.EXPECTED,
    target: str = TARGET,
) -> Backlink:
    return Backlink(
        id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
        version=1,
        source_url=f"{HOST}{path}",
        target_url=target,
        expected_anchor=anchor,
        status=status,
    )


async def check(path: str, **kw):
    b = backlink(path, **kw)
    return evaluate(await MockCrawlerProvider().fetch(b.source_url), b)


async def test_good_article_is_verified() -> None:
    r = await check("/good-article")
    assert r.outcome is Outcome.VERIFIED and r.reasons == ()
    assert r.http_status == 200 and r.link is not None and r.link.rel == ()
    assert r.canonical_url == f"{HOST}/good-article"


async def test_trailing_slash_and_host_case_variants_of_the_target_still_match() -> None:
    b = backlink("/good-article", target="HTTPS://KenResearch.example.test/reports/")
    page = await MockCrawlerProvider().fetch(b.source_url)
    assert evaluate(page, b).outcome is Outcome.VERIFIED


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/nofollow-article", "rel_nofollow_sponsored"),
        ("/anchor-changed", "anchor_mismatch"),
        ("/canonical-elsewhere", "canonical_mismatch"),
    ],
)
async def test_present_but_altered_link_is_changed(path: str, reason: str) -> None:
    r = await check(path)
    assert r.outcome is Outcome.CHANGED and reason in r.reasons


async def test_anchor_is_not_checked_when_none_was_agreed() -> None:
    assert (await check("/anchor-changed", anchor=None)).outcome is Outcome.VERIFIED


@pytest.mark.parametrize(
    ("path", "reason"),
    [("/link-removed", "link_absent"), ("/hidden-link", "link_absent"), ("/gone", "http_404")],
)
async def test_absent_link_or_gone_page_is_missing(path: str, reason: str) -> None:
    r = await check(path)
    assert r.outcome is Outcome.MISSING and r.reasons == (reason,)


async def test_html_comment_injection_on_the_page_changes_nothing() -> None:
    assert (await check("/injected-article")).outcome is Outcome.VERIFIED


def _page(
    status: int, *, robots: bool = True, final: str | None = None, html: str = ""
) -> FetchedPage:
    url = f"{HOST}/good-article"
    return FetchedPage(
        url=url,
        final_url=final or url,
        status=status,
        html=html,
        fetched_at=NOW,
        robots_allowed=robots,
    )


@pytest.mark.parametrize("status", [429, 500, 503, 403])
def test_transient_statuses_are_inconclusive_never_a_miss(status: int) -> None:
    assert evaluate(_page(status), backlink("/good-article")).outcome is Outcome.INCONCLUSIVE


def test_robots_disallow_and_fetch_failure_are_inconclusive() -> None:
    assert evaluate(_page(403, robots=False), backlink("/x")).reasons == ("robots_disallowed",)
    r = evaluate(None, backlink("/x"), fetch_error="ProviderUnavailable")
    assert r.outcome is Outcome.INCONCLUSIVE and r.reasons == ("ProviderUnavailable",)


def test_a_redirect_off_the_agreed_page_is_a_change() -> None:
    html = f'<p><a href="{TARGET}">{ANCHOR}</a></p>'
    page = _page(200, final="https://elsewhere.example.org/moved", html=html)
    r = evaluate(page, backlink("/good-article"))
    assert r.outcome is Outcome.CHANGED and "redirected" in r.reasons


def test_link_extraction_ignores_scripts_comments_and_hidden_elements() -> None:
    facts = analyse_page(
        "<script>var a = \"<a href='https://x.test/1'>s</a>\";</script>"
        '<!-- <a href="/c">c</a> --><div hidden><a href="/h">h</a></div>'
        '<a href="/v" rel="UGC  Nofollow">Visible  link</a>'
    )
    (link,) = facts.links
    assert link.href == "/v" and link.anchor_text == "Visible link"
    assert link.rel == ("nofollow", "ugc")


def test_only_verified_is_ever_active() -> None:
    assert {BacklinkStatus.VERIFIED} == ACTIVE_BACKLINK_STATUSES
    flags = {s: is_active(backlink("/x", status=s)) for s in BacklinkStatus}
    assert [s for s, on in flags.items() if on] == [BacklinkStatus.VERIFIED]
    assert not flags[BacklinkStatus.MISSING] and not flags[BacklinkStatus.LOST]
    assert count_active([backlink("/x", status=s) for s in BacklinkStatus]) == 1
