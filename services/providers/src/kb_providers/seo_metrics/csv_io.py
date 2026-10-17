"""Strict parser for the C12 SEO-metrics CSV (``domain,metric,value,source,fetched_at``).

Three-state discipline (ADR-0015), preserved end to end:

* a row with a number      -> fetched, has a value;
* a row with a blank value -> fetched but unavailable (``SEOMetric.value is None``);
* no row at all            -> never fetched (no ``SEOMetric`` is ever created for it).

Malformed input raises ``InvalidResponse``; rows are never skipped, repaired or defaulted.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from kb_contracts.providers import InvalidResponse, NotConfigured, SEOMetric

CSV_COLUMNS: tuple[str, ...] = ("domain", "metric", "value", "source", "fetched_at")

MetricIndex = dict[str, dict[str, SEOMetric]]
"""``domain -> metric -> SEOMetric`` over exactly the rows present in the file."""


def normalise_key(raw: str) -> str:
    """Domain/metric keys are compared as ``lower(btrim(x))``."""
    return raw.strip().lower()


def read_text(path: Path, what: str) -> str:
    if not path.is_file():
        raise NotConfigured(f"{what} not found at {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidResponse(f"cannot read {what} {path}: {exc}") from exc


def parse_metrics_csv(text: str, origin: str) -> MetricIndex:
    """Parse CSV ``text`` into an index; any structural problem raises ``InvalidResponse``."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise InvalidResponse(f"{origin}: empty file (header row required)") from None
    except csv.Error as exc:
        raise InvalidResponse(f"{origin}: unreadable CSV: {exc}") from exc
    if tuple(h.strip().lower() for h in header) != CSV_COLUMNS:
        raise InvalidResponse(f"{origin}: header must be {','.join(CSV_COLUMNS)}")
    index: MetricIndex = {}
    try:
        for row in reader:
            if not row:  # genuinely blank line, not a malformed row
                continue
            line = reader.line_num
            metric = _parse_row(row, f"{origin}:{line}")
            domain_metrics = index.setdefault(metric.domain, {})
            if metric.metric in domain_metrics:
                raise InvalidResponse(
                    f"{origin}:{line}: duplicate row for {metric.domain}/{metric.metric}"
                )
            domain_metrics[metric.metric] = metric
    except csv.Error as exc:
        raise InvalidResponse(f"{origin}: unreadable CSV: {exc}") from exc
    return index


def _parse_row(row: Sequence[str], where: str) -> SEOMetric:
    if len(row) != len(CSV_COLUMNS):
        raise InvalidResponse(f"{where}: expected {len(CSV_COLUMNS)} fields, got {len(row)}")
    domain, metric, raw_value, source, raw_at = (cell.strip() for cell in row)
    domain = domain.lower()
    metric = metric.lower()
    if not domain or not metric:
        raise InvalidResponse(f"{where}: domain and metric must be non-empty")
    if not source:
        raise InvalidResponse(f"{where}: source must be non-empty")
    value: float | None = None
    if raw_value:
        try:
            value = float(raw_value)
        except ValueError:
            raise InvalidResponse(f"{where}: value {raw_value!r} is not a number") from None
        if not math.isfinite(value):
            raise InvalidResponse(f"{where}: value {raw_value!r} is not finite")
    try:
        fetched_at = datetime.fromisoformat(raw_at)
    except ValueError:
        raise InvalidResponse(f"{where}: fetched_at {raw_at!r} is not ISO-8601") from None
    if fetched_at.tzinfo is None:
        raise InvalidResponse(f"{where}: fetched_at {raw_at!r} must carry a timezone")
    return SEOMetric(
        domain=domain,
        metric=metric,
        value=value,
        source=source,
        fetched_at=fetched_at.astimezone(UTC),
    )


def select_metrics(index: MetricIndex, domain: str, metrics: Sequence[str]) -> list[SEOMetric]:
    """Return only the requested metrics that the index actually holds.

    A requested metric (or domain) absent from the index contributes NO element: it was never
    fetched, which is a different state from ``value=None`` (fetched, unavailable).
    """
    key = normalise_key(domain)
    if not key:
        raise InvalidResponse("domain must be non-empty")
    held = index.get(key)
    if not held:
        return []
    out: list[SEOMetric] = []
    seen: set[str] = set()
    for requested in metrics:
        name = normalise_key(requested)
        if not name or name in seen:
            continue
        seen.add(name)
        found = held.get(name)
        if found is not None:
            out.append(found)
    return out
