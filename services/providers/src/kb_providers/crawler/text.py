"""Minimal, dependency-free HTML -> text reduction for ``FetchedPage.text``.

Stdlib ``html.parser`` only. Script, style and template contents are dropped; HTML comments
are dropped as well, which keeps comment-borne prompt injection out of the plain-text field
(ADR-0020 still treats whatever survives as untrusted data, never instructions).
"""

from __future__ import annotations

from html.parser import HTMLParser

_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "svg"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dd",
        "dl",
        "dt",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [" ".join(line.split()) for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    """Reduce an HTML document to normalised visible text.

    Deterministic: whitespace is collapsed per line and blank lines are removed, so the same
    HTML always yields the same text (and therefore the same content hashes downstream).
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()
