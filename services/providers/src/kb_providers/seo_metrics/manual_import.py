"""``ManualImportSEOMetricsProvider``: reads a user-supplied C12 metrics CSV.

This is the zero-cost, legal path for proprietary metrics (e.g. a user exporting values from
their own licensed tool): the user supplies the file; nothing is scraped. The file is re-read
on every call so a re-import takes effect without restart. Three-state discipline as in
:mod:`kb_providers.seo_metrics.csv_io`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

from kb_contracts.providers import ProviderHealth, SEOMetric

from kb_providers.seo_metrics.csv_io import parse_metrics_csv, read_text, select_metrics


class ManualImportSEOMetricsProvider:
    """CSV-file :class:`kb_contracts.providers.SEOMetricsProvider`.

    Args:
        csv_path: the user-supplied file; a missing file raises ``NotConfigured`` at call time.
    """

    name = "manual_import_seo_metrics"
    is_paid = False

    def __init__(self, csv_path: Path | str) -> None:
        self._path = Path(csv_path).expanduser()

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        ok = self._path.is_file()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderHealth(
            name=self.name,
            status="ok" if ok else "disabled",
            latency_ms=latency_ms,
            detail=None if ok else f"no import file at {self._path}",
        )

    async def get_metrics(self, domain: str, metrics: Sequence[str]) -> list[SEOMetric]:
        text = read_text(self._path, "metrics import file")
        index = parse_metrics_csv(text, str(self._path))
        return select_metrics(index, domain, metrics)
