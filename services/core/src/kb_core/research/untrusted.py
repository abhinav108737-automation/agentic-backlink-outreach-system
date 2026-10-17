"""Defensive handling of untrusted crawled text (ADR-0020).

Crawled pages are DATA. Nothing here interprets text as an instruction; the detector only
decides whether a text span is safe to *quote as evidence / topic / contact source*. Spans that
look like instructions to an AI are withheld from every extraction output and merely counted
for audit (``injection_suspected``).
"""

from __future__ import annotations

import hashlib
import re

_INSTRUCTION_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|all)\b.{0,30}"
        r"\b(instruction|prompt|polic|rule|command)",
        r"\bsystem prompt\b",
        r"\bdeveloper mode\b",
        r"\bnew instructions?\b",
        r"\battention,? (ai|llm|assistant|agent|model)\b",
        r"\b(ai|llm) (assistant|agent|model)\b.{0,40}\b(must|should|now|reading)\b",
        r"\byou (must|are) now\b",
        r"\bskip (the )?(policy|gate|check|review)\b",
        r"\bwithout (human )?(approval|review|a policy check)\b",
        r"\b(approve|mark|set)\b.{0,30}\b(this|the)\b.{0,20}\b(site|domain|prospect|spam)\b",
        r"\bsend (the )?(outreach )?(email )?immediately\b",
    )
)


def looks_like_instruction(text: str) -> bool:
    """True when ``text`` resembles an instruction aimed at an AI agent."""
    return any(p.search(text) for p in _INSTRUCTION_PATTERNS)


def normalise_space(text: str) -> str:
    return " ".join(text.split())


def content_hash(text: str) -> str:
    """Lowercase hex SHA-256 of whitespace-normalised UTF-8 text (evidence ``content_hash``)."""
    return hashlib.sha256(normalise_space(text).encode("utf-8")).hexdigest()
