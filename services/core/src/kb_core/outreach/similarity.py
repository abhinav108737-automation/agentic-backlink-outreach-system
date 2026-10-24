"""Deterministic text similarity for the duplicate-message check. Stdlib only."""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9']+")
SHINGLE = 3


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    if len(words) < SHINGLE:
        return frozenset({tuple(words)}) if words else frozenset()
    return frozenset(tuple(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1))


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of word 3-shingles, in [0, 1]. Identical text scores 1.0."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
