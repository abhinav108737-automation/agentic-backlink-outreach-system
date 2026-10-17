"""Single-pass, stdlib-only HTML scan shared by research and contact extraction.

Produces a neutral structural view of a page (title, meta, headings, paragraphs, bylines,
links, text nodes) while *excluding* content a human reader would not see as the site's own
voice: hidden elements, scripts/styles/templates, HTML comments, attribute text (``alt`` etc.)
and, for ``in_quote`` blocks, quoted material. Excluded text is never returned except as a
count of instruction-like spans (``suspicious_spans``) for audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from kb_core.research.untrusted import looks_like_instruction, normalise_space

_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_SKIP = frozenset({"script", "style", "template", "noscript", "svg", "head"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"})
_BYLINE_TAGS = frozenset({"p", "span", "div", "address", "a", "small", "li"})


@dataclass(frozen=True)
class TextNode:
    text: str
    in_quote: bool


@dataclass(frozen=True)
class Block:
    tag: str
    text: str
    in_quote: bool


@dataclass(frozen=True)
class Link:
    href: str
    text: str
    in_quote: bool


@dataclass
class ScannedPage:
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    bylines: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    text_nodes: list[TextNode] = field(default_factory=list)
    suspicious_spans: int = 0


@dataclass
class _Frame:
    tag: str
    hidden: bool
    quote: bool
    kind: str | None = None  # "block" | "byline" | "link" | "title"
    href: str = ""
    parts: list[str] = field(default_factory=list)


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ScannedPage()
        self._stack: list[_Frame] = []
        self._in_title = False
        self._title_parts: list[str] = []

    # -- helpers -------------------------------------------------------------------------
    def _suppressed(self) -> bool:
        return any(f.hidden for f in self._stack)

    def _in_quote(self) -> bool:
        return any(f.tag == "blockquote" for f in self._stack)

    def _skip_depth(self) -> bool:
        return any(f.tag in _SKIP and f.tag != "head" for f in self._stack)

    # -- parser callbacks ----------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and "content" in a and not self._suppressed():
                self.page.meta.setdefault(name, normalise_space(a["content"]))
            return
        if tag in _VOID:
            return
        if tag == "title":
            self._in_title = True
        hidden = (
            tag in _SKIP
            and tag != "head"
            or "hidden" in a
            or a.get("aria-hidden", "").lower() == "true"
            or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        )
        frame = _Frame(tag=tag, hidden=hidden, quote=tag == "blockquote")
        classes = a.get("class", "").lower().split()
        is_byline = tag in _BYLINE_TAGS and (
            any("byline" in c for c in classes)
            or a.get("rel", "").lower() == "author"
            or a.get("itemprop", "").lower() == "author"
        )
        if tag == "a":
            frame.kind = "link"
            frame.href = a.get("href", "")
        elif is_byline:
            frame.kind = "byline"
        elif tag in _BLOCK_TAGS:
            frame.kind = "block"
        self._stack.append(frame)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag == "title":
            self._in_title = False
            if not self.page.title:
                self.page.title = normalise_space("".join(self._title_parts))
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                closing = self._stack[i:]
                del self._stack[i:]
                for frame in reversed(closing):
                    self._finish(frame)
                return

    def _finish(self, frame: _Frame) -> None:
        if frame.kind is None:
            return
        text = normalise_space("".join(frame.parts))
        if not text and (frame.kind != "link" or not frame.href):
            return
        if frame.hidden or self._suppressed():
            return
        quote = frame.quote or self._in_quote()
        if frame.kind == "link":
            self.page.links.append(Link(href=frame.href.strip(), text=text, in_quote=quote))
        elif frame.kind == "byline":
            if not quote and not looks_like_instruction(text):
                self.page.bylines.append(text)
        elif not looks_like_instruction(text):
            self.page.blocks.append(Block(tag=frame.tag, text=text, in_quote=quote))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        text = normalise_space(data)
        if not text:
            return
        if looks_like_instruction(text):
            self.page.suspicious_spans += 1
            return  # withheld from every output, including parent block text
        if self._suppressed() or self._skip_depth() or any(f.tag == "head" for f in self._stack):
            return
        for frame in self._stack:
            if frame.kind is not None:
                frame.parts.append(data)
        self.page.text_nodes.append(TextNode(text=text, in_quote=self._in_quote()))

    def handle_comment(self, data: str) -> None:
        if looks_like_instruction(data):
            self.page.suspicious_spans += 1


def scan_html(html: str) -> ScannedPage:
    """Scan ``html``; never raises on malformed markup."""
    scanner = _Scanner()
    scanner.feed(html)
    scanner.close()
    while scanner._stack:  # unclosed elements still yield their content
        scanner._finish(scanner._stack.pop())
    return scanner.page
