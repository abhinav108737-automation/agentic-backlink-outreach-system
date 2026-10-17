"""Discovery helpers: URL canonicalization, registered domains, dedupe (deterministic)."""

from kb_core.discovery.canonical import (
    CANONICALIZATION_VERSION,
    CanonicalUrl,
    InvalidUrl,
    canonicalize_url,
    dedupe_candidates,
    group_by_registered_domain,
    registered_domain,
    url_hash,
)

__all__ = [
    "CANONICALIZATION_VERSION",
    "CanonicalUrl",
    "InvalidUrl",
    "canonicalize_url",
    "dedupe_candidates",
    "group_by_registered_domain",
    "registered_domain",
    "url_hash",
]
