"""``MockCrawlerProvider``: serves ``fixtures/sites/<domain>/`` HTML bundles (C5 + C12).

Offline by construction, and robots-aware: a site bundle whose ``site.json`` sets
``"robots_allowed": false`` yields an empty, ``robots_allowed=False`` page instead of content,
mirroring what the real robots-aware httpx crawler must do.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from kb_contracts.providers import (
    FetchedPage,
    InvalidResponse,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)

from kb_providers.crawler.text import html_to_text
from kb_providers.fixtures import load_json_object, require_dir, resolve_root

FIXTURE_FETCHED_AT = datetime(2026, 1, 5, tzinfo=UTC)
"""Fallback timestamp so fixture fetches are reproducible (no wall clock in test output)."""

ROBOTS_BLOCKED_STATUS = 403
"""Status reported for a robots-disallowed fixture fetch; no body is ever returned."""


class MockCrawlerProvider:
    """Fixture-backed :class:`kb_contracts.providers.CrawlerProvider`.

    Args:
        fixture_root: corpus root; defaults to the repository ``fixtures/`` directory.
        now: fixed timestamp for ``FetchedPage.fetched_at``; defaults to the bundle's
            ``fetched_at`` field, then to :data:`FIXTURE_FETCHED_AT`.
        simulate: when given, ``fetch`` raises this taxonomy error.
    """

    name = "mock_crawler"
    is_paid = False

    def __init__(
        self,
        fixture_root: Path | str | None = None,
        *,
        now: datetime | None = None,
        simulate: type[ProviderError] | None = None,
    ) -> None:
        self._root = resolve_root(fixture_root)
        self._now = now
        self._simulate = simulate

    @property
    def sites_dir(self) -> Path:
        """Directory holding the HTML site bundles."""
        return self._root / "sites"

    async def health(self) -> ProviderHealth:
        if self._simulate is not None:
            return ProviderHealth(
                name=self.name, status="down", detail=f"simulated {self._simulate.__name__}"
            )
        started = time.perf_counter()
        ok = self.sites_dir.is_dir()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderHealth(
            name=self.name,
            status="ok" if ok else "down",
            latency_ms=latency_ms,
            detail=None if ok else f"no site fixtures at {self.sites_dir}",
        )

    async def fetch(self, url: str) -> FetchedPage:
        """Fetch a fixture page.

        Outcomes:
            * unknown host -> ``ProviderUnavailable`` (nothing in the corpus answers for it);
            * robots-disallowed bundle -> status 403, empty body, ``robots_allowed=False``;
            * host known but path absent -> status 404, empty body;
            * otherwise status 200 with the bundle HTML and its reduced text.
        """
        if self._simulate is not None:
            raise self._simulate(f"simulated failure for {self.name}")
        sites_dir = require_dir(self.sites_dir, "site fixture directory")
        host, path = _split_target(url)
        site_dir = _resolve_site_dir(sites_dir, host)
        if site_dir is None:
            raise ProviderUnavailable(f"no fixture site bundle for host {host!r}")
        meta = load_json_object(site_dir / "site.json")
        fetched_at = self._resolve_fetched_at(meta, site_dir)
        if meta.get("robots_allowed", True) is False:
            return FetchedPage(
                url=url,
                final_url=url,
                status=ROBOTS_BLOCKED_STATUS,
                html="",
                text="",
                fetched_at=fetched_at,
                robots_allowed=False,
            )
        pages = meta.get("pages")
        if not isinstance(pages, dict):
            raise InvalidResponse(f"{site_dir / 'site.json'} has no 'pages' object")
        filename = pages.get(path)
        if filename is None and path != "/":
            filename = pages.get(path.rstrip("/") or "/")
        if not isinstance(filename, str):
            return FetchedPage(
                url=url,
                final_url=url,
                status=404,
                html="",
                text="",
                fetched_at=fetched_at,
                robots_allowed=True,
            )
        html_path = site_dir / filename
        if not html_path.is_file():
            raise InvalidResponse(f"{site_dir / 'site.json'} points at missing file {filename!r}")
        html = html_path.read_text(encoding="utf-8")
        return FetchedPage(
            url=url,
            final_url=url,
            status=200,
            html=html,
            text=html_to_text(html),
            fetched_at=fetched_at,
            robots_allowed=True,
        )

    def _resolve_fetched_at(self, meta: dict[str, object], site_dir: Path) -> datetime:
        if self._now is not None:
            return self._now
        raw = meta.get("fetched_at")
        if isinstance(raw, str) and raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError as exc:
                raise InvalidResponse(
                    f"{site_dir / 'site.json'} has an unparseable fetched_at {raw!r}"
                ) from exc
        return FIXTURE_FETCHED_AT


def _split_target(url: str) -> tuple[str, str]:
    """Split a URL into (lowercased host, path) for fixture lookup."""
    parts = urlsplit(url if "//" in url else f"https://{url}")
    host = parts.hostname or ""
    if not host:
        raise InvalidResponse(f"cannot determine a host for {url!r}")
    path = unquote(parts.path) or "/"
    return host.lower(), path


def _resolve_site_dir(sites_dir: Path, host: str) -> Path | None:
    """Find the bundle for ``host``, tolerating a present/absent ``www.`` prefix."""
    candidates = [host]
    if host.startswith("www."):
        candidates.append(host[4:])
    else:
        candidates.append(f"www.{host}")
    for candidate in candidates:
        site_dir = sites_dir / candidate
        if (site_dir / "site.json").is_file():
            return site_dir
    return None
