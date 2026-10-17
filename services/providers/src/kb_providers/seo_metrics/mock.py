"""``MockSEOMetricsProvider``: serves ``fixtures/metrics/<dataset>.csv`` (C5 + C12).

Offline and free (``is_paid=False``). Preserves the three metric states exactly; see
:mod:`kb_providers.seo_metrics.csv_io`. It never fabricates, interpolates or carries forward a
value, and it never scrapes Moz.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

from kb_contracts.providers import ProviderError, ProviderHealth, SEOMetric

from kb_providers.fixtures import resolve_root
from kb_providers.seo_metrics.csv_io import parse_metrics_csv, read_text, select_metrics


class MockSEOMetricsProvider:
    """Fixture-backed :class:`kb_contracts.providers.SEOMetricsProvider`.

    Args:
        fixture_root: corpus root; defaults to the repository ``fixtures/`` directory.
        dataset: CSV basename under ``metrics/`` (default ``baseline``).
        simulate: when given, every call raises this taxonomy error.
    """

    name = "mock_seo_metrics"
    is_paid = False

    def __init__(
        self,
        fixture_root: Path | str | None = None,
        *,
        dataset: str = "baseline",
        simulate: type[ProviderError] | None = None,
    ) -> None:
        self._root = resolve_root(fixture_root)
        self._dataset = dataset
        self._simulate = simulate

    @property
    def csv_path(self) -> Path:
        return self._root / "metrics" / f"{self._dataset}.csv"

    async def health(self) -> ProviderHealth:
        if self._simulate is not None:
            return ProviderHealth(
                name=self.name, status="down", detail=f"simulated {self._simulate.__name__}"
            )
        started = time.perf_counter()
        ok = self.csv_path.is_file()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderHealth(
            name=self.name,
            status="ok" if ok else "down",
            latency_ms=latency_ms,
            detail=None if ok else f"no metrics fixture at {self.csv_path}",
        )

    async def get_metrics(self, domain: str, metrics: Sequence[str]) -> list[SEOMetric]:
        """Return the fixture rows for ``domain`` restricted to ``metrics``.

        Raises ``NotConfigured`` when the CSV is absent and ``InvalidResponse`` when it is
        malformed or ``domain`` is empty.
        """
        if self._simulate is not None:
            raise self._simulate(f"simulated failure for {self.name}")
        text = read_text(self.csv_path, "metrics fixture")
        index = parse_metrics_csv(text, str(self.csv_path))
        return select_metrics(index, domain, metrics)
