"""Deterministic research extraction from a ``FetchedPage`` (no LLM, stdlib parsing only).

Pure function: the same page always yields the same result. Output maps onto the frozen
``EvidenceItem`` (``source_url``, ``excerpt``, ``content_hash``, ``kind``); the persistence layer
adds ``id``/``prospect_id``/``extracted_at``/``extractor``.

Untrusted-data guarantees (ADR-0020): editorial signals come only from page *structure*
(URL path, page title/headings, link targets, byline elements), never from sentences in body
text; hidden elements, comments, attribute text, quoted blocks and instruction-like spans are
excluded from topics, signals and evidence. ``injection_suspected`` merely records that such
spans were seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from kb_contracts.providers import FetchedPage

from kb_core.research.htmlscan import Block, ScannedPage, scan_html
from kb_core.research.untrusted import content_hash, looks_like_instruction

MAX_EXCERPT_CHARS = 300
MAX_BODY_EXCERPTS = 2
MAX_TOPICS = 12

_WFU_PATH = re.compile(
    r"write[-_ ]?for[-_ ]?us|guest[-_ ]?post|contribut|submit[-_ ]?(an[-_ ])?(article|post)|"
    r"become[-_ ]?a[-_ ]?(writer|contributor)",
    re.IGNORECASE,
)
_CONTACT_PATH = re.compile(r"(^|/)(contact|contact[-_ ]us|get[-_ ]in[-_ ]touch)(/|$|\.)", re.I)
_BYLINE_PREFIX = re.compile(
    r"^(written\s+by|posted\s+by|by|author|editor|authors|edited\s+by)\s*:?\s+", re.IGNORECASE
)
_GENERIC_KEYWORDS = frozenset(
    {"write for us", "guest post", "guest posts", "contact", "about", "home", "notes", "analysis"}
)


@dataclass(frozen=True)
class EvidenceExcerpt:
    source_url: str
    excerpt: str
    content_hash: str
    kind: str


@dataclass(frozen=True)
class EditorialSignals:
    is_write_for_us_page: bool
    links_to_write_for_us: bool
    is_contact_page: bool
    links_to_contact_page: bool
    bylines: tuple[str, ...]


@dataclass(frozen=True)
class ResearchExtraction:
    source_url: str
    title: str
    topics: tuple[str, ...]
    signals: EditorialSignals
    evidence: tuple[EvidenceExcerpt, ...]
    injection_suspected: bool


def _clip(text: str) -> str:
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[: MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def _evidence(url: str, text: str, kind: str) -> EvidenceExcerpt | None:
    excerpt = _clip(" ".join(text.split()))
    if not excerpt or looks_like_instruction(excerpt):
        return None
    return EvidenceExcerpt(
        source_url=url, excerpt=excerpt, content_hash=content_hash(excerpt), kind=kind
    )


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def _clean_byline(raw: str) -> str:
    name = _BYLINE_PREFIX.sub("", raw.strip())
    return name.strip(" .,;:")


def _topics(page: ScannedPage) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for kw in page.meta.get("keywords", "").split(","):
        topic = " ".join(kw.lower().split())
        if topic and topic not in _GENERIC_KEYWORDS and not looks_like_instruction(topic):
            seen.setdefault(topic, None)
    return tuple(list(seen)[:MAX_TOPICS])


def extract_research(page: FetchedPage) -> ResearchExtraction:
    """Extract topics, editorial signals and evidence excerpts from ``page``.

    A page that was robots-blocked or not fetched successfully (``robots_allowed`` false or
    status not 2xx) yields an empty extraction rather than guesses.
    """
    url = page.final_url or page.url
    empty = EditorialSignals(False, False, False, False, ())
    if not page.robots_allowed or not 200 <= page.status < 300 or not page.html.strip():
        return ResearchExtraction(url, "", (), empty, (), False)

    scanned = scan_html(page.html)
    path = _path(url)
    headings = [b for b in scanned.blocks if b.tag in {"h1", "h2"} and not b.in_quote]
    h1 = next((b.text for b in headings if b.tag == "h1"), "")
    title = scanned.title if not looks_like_instruction(scanned.title) else ""

    def heading_matches(rx: re.Pattern[str]) -> bool:
        return any(rx.search(t) for t in (title, h1))

    is_wfu = bool(_WFU_PATH.search(path)) or heading_matches(_WFU_PATH)
    is_contact = bool(_CONTACT_PATH.search(path)) or heading_matches(_CONTACT_PATH)
    link_targets = [
        (link.href, link.text) for link in scanned.links if link.href and not link.in_quote
    ]
    links_wfu = any(_WFU_PATH.search(href) or _WFU_PATH.search(text) for href, text in link_targets)
    links_contact = any(
        _CONTACT_PATH.search(urlsplit(href).path) or _CONTACT_PATH.search(f"/{text}")
        for href, text in link_targets
    )
    bylines = tuple(dict.fromkeys(n for n in (_clean_byline(b) for b in scanned.bylines) if n))

    evidence: list[EvidenceExcerpt] = []

    def add(text: str, kind: str) -> None:
        item = _evidence(url, text, kind)
        if item is not None and item not in evidence:
            evidence.append(item)

    add(title, "title")
    add(scanned.meta.get("description", ""), "meta_description")
    for block in headings:
        add(block.text, "heading")
    for name in bylines:
        add(f"By {name}", "byline")
    paragraphs: list[Block] = [b for b in scanned.blocks if b.tag == "p" and not b.in_quote]
    body_kind = "editorial_guidelines" if is_wfu else "body_excerpt"
    for block in paragraphs[:MAX_BODY_EXCERPTS]:
        add(block.text, body_kind)

    return ResearchExtraction(
        source_url=url,
        title=title,
        topics=_topics(scanned),
        signals=EditorialSignals(
            is_write_for_us_page=is_wfu,
            links_to_write_for_us=links_wfu,
            is_contact_page=is_contact,
            links_to_contact_page=links_contact,
            bylines=bylines,
        ),
        evidence=tuple(evidence),
        injection_suspected=scanned.suspicious_spans > 0,
    )
