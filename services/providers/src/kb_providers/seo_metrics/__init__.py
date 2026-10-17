"""SEO-metrics providers (C5): fixture mock and manual CSV import. No scraping, no paid APIs."""

from kb_providers.seo_metrics.manual_import import ManualImportSEOMetricsProvider
from kb_providers.seo_metrics.mock import MockSEOMetricsProvider

__all__ = ["ManualImportSEOMetricsProvider", "MockSEOMetricsProvider"]
