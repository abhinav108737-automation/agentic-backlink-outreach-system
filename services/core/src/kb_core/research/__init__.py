"""Deterministic research extraction (no LLM). Crawled content is untrusted data (ADR-0020)."""

from kb_core.research.extract import (
    EditorialSignals,
    EvidenceExcerpt,
    ResearchExtraction,
    extract_research,
)
from kb_core.research.untrusted import content_hash, looks_like_instruction

__all__ = [
    "EditorialSignals",
    "EvidenceExcerpt",
    "ResearchExtraction",
    "content_hash",
    "extract_research",
    "looks_like_instruction",
]
