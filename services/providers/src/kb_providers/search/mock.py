"""``MockSearchProvider``: serves ``fixtures/serp/<query-slug>.json`` (C5 + C12).

Entirely offline. The provider never performs network I/O, so it is safe in CI and is the
MVP default behind the SearchProvider protocol.
"""

from __future__ import annotations

import time
from pathlib import Path

from kb_contracts.providers import (
    InvalidResponse,
    ProviderError,
    ProviderHealth,
    SearchResult,
)
from pydantic import ValidationError

from kb_providers.fixtures import (
    load_json_object,
    require_dir,
    resolve_root,
    slugify_query,
)


class MockSearchProvider:
    """Fixture-backed :class:`kb_contracts.providers.SearchProvider`.

    Args:
        fixture_root: corpus root; defaults to the repository ``fixtures/`` directory.
        simulate: when given, every call raises this taxonomy error. Downstream lanes use it to
            exercise their provider-failure handling without a network or a patched module.
    """

    name = "mock_search"
    is_paid = False

    def __init__(
        self,
        fixture_root: Path | str | None = None,
        *,
        simulate: type[ProviderError] | None = None,
    ) -> None:
        self._root = resolve_root(fixture_root)
        self._simulate = simulate

    @property
    def serp_dir(self) -> Path:
        """Directory holding the SERP fixture sets."""
        return self._root / "serp"

    async def health(self) -> ProviderHealth:
        if self._simulate is not None:
            return ProviderHealth(
                name=self.name, status="down", detail=f"simulated {self._simulate.__name__}"
            )
        started = time.perf_counter()
        ok = self.serp_dir.is_dir()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderHealth(
            name=self.name,
            status="ok" if ok else "down",
            latency_ms=latency_ms,
            detail=None if ok else f"no SERP fixtures at {self.serp_dir}",
        )

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Return the fixture SERP set for ``query``.

        An unknown query is not an error: the corpus simply holds no results for it, so an empty
        list is returned. Structural problems in a fixture raise ``InvalidResponse``; a missing
        corpus raises ``NotConfigured``.
        """
        if self._simulate is not None:
            raise self._simulate(f"simulated failure for {self.name}")
        if limit < 0:
            raise InvalidResponse("limit must be >= 0")
        serp_dir = require_dir(self.serp_dir, "SERP fixture directory")
        slug = slugify_query(query)
        if not slug:
            return []
        path = serp_dir / f"{slug}.json"
        if not path.is_file():
            return []
        payload = load_json_object(path)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise InvalidResponse(f"fixture {path} has no 'results' list")
        fixture_query = payload.get("query")
        results: list[SearchResult] = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                raise InvalidResponse(f"fixture {path} has a non-object result entry")
            if not entry.get("query") and isinstance(fixture_query, str):
                entry = {**entry, "query": fixture_query}
            try:
                results.append(SearchResult.model_validate(entry))
            except ValidationError as exc:
                raise InvalidResponse(f"fixture {path} result failed validation: {exc}") from exc
        results.sort(key=lambda r: (r.rank, r.url))
        return results[:limit]
