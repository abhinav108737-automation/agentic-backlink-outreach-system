"""CrawlerProvider implementations (Lane E). Offline mock over the C12 HTML site bundles."""

from __future__ import annotations

from kb_providers.crawler.mock import MockCrawlerProvider
from kb_providers.crawler.text import html_to_text

__all__ = ["MockCrawlerProvider", "html_to_text"]
