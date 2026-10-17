"""Mock search and crawler providers: happy paths and every taxonomy branch they can raise."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from kb_contracts.providers import (
    AuthRequired,
    CrawlerProvider,
    InvalidResponse,
    NotConfigured,
    ProviderUnavailable,
    RateLimited,
    SearchProvider,
)
from kb_providers.crawler import MockCrawlerProvider
from kb_providers.fixtures import slugify_query
from kb_providers.search import MockSearchProvider

SIMULATED = [ProviderUnavailable, RateLimited, AuthRequired, NotConfigured]


# ------------------------------------------------------------------ search
async def test_search_happy_path_sorted_and_limited() -> None:
    p = MockSearchProvider()
    assert isinstance(p, SearchProvider) and p.is_paid is False
    results = await p.search("analytics guest post", limit=2)
    assert len(results) == 2
    assert [r.rank for r in results] == sorted(r.rank for r in results)
    assert all(r.query == "analytics guest post" for r in results)
    assert (await p.health()).status == "ok"


async def test_search_unknown_query_is_empty_not_error() -> None:
    assert await MockSearchProvider().search("zzz no such query") == []
    assert await MockSearchProvider().search("!!!") == []


async def test_search_negative_limit_invalid_response() -> None:
    with pytest.raises(InvalidResponse):
        await MockSearchProvider().search("analytics guest post", limit=-1)


async def test_search_missing_corpus_not_configured(tmp_path: Path) -> None:
    p = MockSearchProvider(tmp_path)
    with pytest.raises(NotConfigured):
        await p.search("x")
    assert (await p.health()).status == "down"


@pytest.mark.parametrize(
    "payload", ["{not json", "[]", json.dumps({"query": "q"}), json.dumps({"results": [1]}),
                json.dumps({"results": [{"rank": "x"}]})],
    ids=["bad-json", "not-object", "no-results", "non-object-entry", "fails-validation"],
)  # fmt: skip
async def test_search_malformed_fixture_invalid_response(tmp_path: Path, payload: str) -> None:
    (tmp_path / "serp").mkdir()
    (tmp_path / "serp" / "q.json").write_text(payload)
    with pytest.raises(InvalidResponse):
        await MockSearchProvider(tmp_path).search("q")


@pytest.mark.parametrize("err", SIMULATED)
async def test_search_simulated_errors(err: type[Exception]) -> None:
    p = MockSearchProvider(simulate=err)  # type: ignore[arg-type]
    with pytest.raises(err):
        await p.search("q")
    assert (await p.health()).status == "down"


def test_slugify_query_is_stable() -> None:
    assert slugify_query("  Analytics -- Guest_Post! ") == "analytics-guest-post"


# ------------------------------------------------------------------ crawler
async def test_crawler_happy_path_html_and_text() -> None:
    c = MockCrawlerProvider()
    assert isinstance(c, CrawlerProvider) and c.is_paid is False
    page = await c.fetch("https://sectorbriefing.co.uk/write-for-us")
    assert page.status == 200 and page.robots_allowed
    assert "<html" in page.html and "Write for us" in page.text and "<" not in page.text
    assert page.fetched_at == datetime(2026, 1, 5, tzinfo=UTC)
    assert (await c.health()).status == "ok"


async def test_crawler_www_tolerance_and_trailing_slash() -> None:
    c = MockCrawlerProvider()
    assert (await c.fetch("https://energyoutlookdaily.com/")).status == 200
    assert (await c.fetch("https://sectorbriefing.co.uk/contact/")).status == 200


async def test_crawler_known_host_missing_path_is_404() -> None:
    page = await MockCrawlerProvider().fetch("https://sectorbriefing.co.uk/nope")
    assert page.status == 404 and page.html == ""


async def test_crawler_robots_disallowed_returns_no_content() -> None:
    page = await MockCrawlerProvider().fetch("https://hobbyistgardenpages.org/")
    assert page.robots_allowed is False and page.html == "" and page.text == ""


async def test_crawler_unknown_host_unavailable() -> None:
    with pytest.raises(ProviderUnavailable):
        await MockCrawlerProvider().fetch("https://not-in-corpus.example/")


async def test_crawler_hostless_url_invalid_response() -> None:
    with pytest.raises(InvalidResponse):
        await MockCrawlerProvider().fetch("https:///path")


async def test_crawler_missing_corpus_not_configured(tmp_path: Path) -> None:
    c = MockCrawlerProvider(tmp_path)
    with pytest.raises(NotConfigured):
        await c.fetch("https://a.example/")
    assert (await c.health()).status == "down"


def _site(tmp_path: Path, meta: object, files: dict[str, str] | None = None) -> Path:
    site = tmp_path / "sites" / "a.example"
    site.mkdir(parents=True)
    (site / "site.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))
    for name, body in (files or {}).items():
        (site / name).write_text(body)
    return tmp_path


@pytest.mark.parametrize(
    "meta",
    ["{bad", json.dumps({"domain": "a.example"}), {"pages": {"/": "gone.html"}},
     {"pages": {"/": "i.html"}, "fetched_at": "not-a-date"}],
    ids=["bad-json", "no-pages", "dangling-file", "bad-fetched-at"],
)  # fmt: skip
async def test_crawler_malformed_bundle_invalid_response(tmp_path: Path, meta: object) -> None:
    root = _site(tmp_path, meta, {"i.html": "<p>x</p>"})
    with pytest.raises(InvalidResponse):
        await MockCrawlerProvider(root).fetch("https://a.example/")


async def test_crawler_now_override(tmp_path: Path) -> None:
    root = _site(tmp_path, {"pages": {"/": "i.html"}}, {"i.html": "<p>x</p>"})
    fixed = datetime(2030, 2, 3, tzinfo=UTC)
    page = await MockCrawlerProvider(root, now=fixed).fetch("https://a.example/")
    assert page.fetched_at == fixed


@pytest.mark.parametrize("err", SIMULATED)
async def test_crawler_simulated_errors(err: type[Exception]) -> None:
    c = MockCrawlerProvider(simulate=err)  # type: ignore[arg-type]
    with pytest.raises(err):
        await c.fetch("https://a.example/")
    assert (await c.health()).status == "down"
