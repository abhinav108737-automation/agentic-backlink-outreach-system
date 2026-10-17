"""Deterministic contact extraction (no LLM). Crawled content is untrusted data (ADR-0020)."""

from kb_core.contacts.extract import (
    CHANNEL_CONTACT_PAGE,
    CHANNEL_EMAIL,
    ContactEvidence,
    ExtractedContact,
    extract_contacts,
    normalise_value,
    to_contact_candidates,
)

__all__ = [
    "CHANNEL_CONTACT_PAGE",
    "CHANNEL_EMAIL",
    "ContactEvidence",
    "ExtractedContact",
    "extract_contacts",
    "normalise_value",
    "to_contact_candidates",
]
