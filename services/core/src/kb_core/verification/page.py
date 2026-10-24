"""Stdlib-only link extraction for backlink verification.

Reads a fetched page the way a crawler would: visible anchors only (scripts, styles, templates,
comments and hidden elements are ignored), each with its `rel` tokens and anchor text, plus the
page's `<link rel="canonical">`. The page is untrusted data; nothing here follows or obeys it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

_SKIP = frozenset({"script", "style", "template", "noscript", "svg"})
_HIDDEN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr", "source", "track", "area"})
_BLOCK = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "td", "blockquote", "section"})


@dataclass(frozen=True)
class FoundLink:
    href: str
    anchor_text: str
    rel: tuple[str, ...]
    context: str


@dataclass(frozen=True)
class PageFacts:
    canonical_href: str | None
    links: tuple[FoundLink, ...]


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[FoundLink] = []
        self.canonical: str | None = None
        self._hidden = 0
        self._stack: list[bool] = []
        self._block = ""
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []
        self._before = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link":
            rels = a.get("rel", "").lower().split()
            if "canonical" in rels and self.canonical is None and a.get("href"):
                self.canonical = a["href"].strip()
            return
        if tag in _VOID:
            return
        hidden = (
            tag in _SKIP
            or "hidden" in a
            or a.get("aria-hidden") == "true"
            or bool(_HIDDEN.search(a.get("style", "")))
        )
        self._stack.append(hidden)
        if hidden:
            self._hidden += 1
            return
        if tag in _BLOCK:
            self._block = ""
        if tag == "a" and self._hidden == 0 and a.get("href"):
            self._anchor = a
            self._anchor_text = []
            self._before = self._block[-80:]

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or not self._stack:
            return
        if self._stack.pop():
            self._hidden -= 1
            return
        if tag == "a" and self._anchor is not None:
            text = " ".join("".join(self._anchor_text).split())
            context = " ".join(f"{self._before} {text}".split())
            self.links.append(
                FoundLink(
                    href=self._anchor["href"].strip(),
                    anchor_text=text,
                    rel=tuple(sorted(set(self._anchor.get("rel", "").lower().split()))),
                    context=context[:200],
                )
            )
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        self._block += data
        if self._anchor is not None:
            self._anchor_text.append(data)


def analyse_page(html: str) -> PageFacts:
    scan = _Scan()
    scan.feed(html)
    scan.close()
    return PageFacts(scan.canonical, tuple(scan.links))
