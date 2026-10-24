"""Turn an untrusted email body into stored text and into rule input (ADR-0020).

An email body is DATA. Two views are derived from it, and neither is ever interpreted as an
instruction:

* `sanitize_email_body` -> what is stored in `replies.body_sanitized`: HTML stripped (scripts,
  styles, comments and hidden elements dropped), control characters removed, quoted history
  removed, length capped. Instruction-like text is *kept* here (it is part of what the sender
  wrote and must remain auditable).
* `authored_text` -> what the classification rules may read: the same text with every line or
  sentence that looks like an instruction aimed at an AI removed. A rule can therefore never be
  triggered, redirected or suppressed by text written to steer a model.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from kb_core.research.untrusted import looks_like_instruction

MAX_BODY_CHARS = 20_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_QUOTE_START = re.compile(r"^\s*(>|-{2,}\s*original message\s*-{2,}|on .{5,120} wrote:\s*$)", re.I)
_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "svg", "head"})
_BREAK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"})
_HIDDEN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0", re.I)
_VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr"})

# Line-level markers of text addressed to a model rather than a human editor.
_LINE_INJECTION = tuple(
    re.compile(p, re.I)
    for p in (
        r"^\s*(system|assistant|developer|instruction)s?\s*[:>\]]",
        r"</?\s*(system|assistant|instructions?)\s*>",
        r"\[\s*(inst|system)\s*\]",
    )
)
# Sentence-level steering phrases (complements `looks_like_instruction`).
_SENTENCE_INJECTION = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bclassify\b.{0,40}\bas\b",
        r"\b(add|put|place)\b.{0,50}\bsuppression\b",
        r"\bapprove\b.{0,40}\b(payment|commitment|request|deal)\b",
        r"\bignore\b.{0,40}\b(rules?|instructions?|polic\w*|guidelines?)\b",
        r"\bas an? (ai|assistant|language model)\b",
        r"\byou (should|must|will) (now )?(classify|mark|treat|approve|ignore|reply)\b",
        r"\b(set|mark)\b.{0,30}\b(class|classification|label)\b",
    )
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._stack: list[bool] = []  # per open element: does it contribute to the skip depth

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            if tag == "br" and not self._skip:
                self.parts.append("\n")
            return
        attr = {k: (v or "") for k, v in attrs}
        hidden = (
            tag in _SKIP_TAGS
            or "hidden" in attr
            or attr.get("aria-hidden") == "true"
            or bool(_HIDDEN.search(attr.get("style", "")))
        )
        self._stack.append(hidden)
        if hidden:
            self._skip += 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or not self._stack:
            return
        if self._stack.pop():
            self._skip -= 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _strip_html(raw: str) -> str:
    if "<" not in raw:
        return raw
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)  # comments are never passed to handle_data


def sanitize_email_body(raw: str, *, max_chars: int = MAX_BODY_CHARS) -> str:
    text = _CONTROL.sub("", _strip_html(raw))
    kept: list[str] = []
    for line in text.splitlines():
        if _QUOTE_START.match(line):
            if line.lstrip().startswith(">"):
                continue  # quoted history line
            break  # "On ... wrote:" / "Original Message": everything after is history
        kept.append(line.rstrip())
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out[:max_chars]


def _is_steering(segment: str) -> bool:
    return looks_like_instruction(segment) or any(p.search(segment) for p in _SENTENCE_INJECTION)


def authored_text(body_sanitized: str) -> tuple[str, int]:
    """Return (rule input, number of dropped instruction-like segments)."""
    dropped = 0
    lines: list[str] = []
    for line in body_sanitized.splitlines():
        if any(p.search(line) for p in _LINE_INJECTION):
            dropped += 1
            continue
        kept: list[str] = []
        for sentence in _SENTENCE_SPLIT.split(line):
            if sentence and _is_steering(sentence):
                dropped += 1
            else:
                kept.append(sentence)
        lines.append(" ".join(kept))
    return "\n".join(lines).strip(), dropped
