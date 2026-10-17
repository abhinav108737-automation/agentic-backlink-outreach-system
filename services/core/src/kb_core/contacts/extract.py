"""Deterministic contact extraction from a ``FetchedPage`` (no LLM, stdlib only).

Emits email and contact-page channels. Every ``value`` is normalised as ``lower(btrim(x))``,
matching the DB CHECK on ``suppression_entries`` and the ``contact_channels``
``UNIQUE(contact_id, channel_type, value_normalized)`` key. Candidates are deduplicated on
``(channel_type, value)`` keeping the highest confidence.

Untrusted-data guarantees (ADR-0020): addresses are read only from visible, non-quoted text and
``mailto:`` links outside hidden elements; text spans that read like instructions to an AI are
withheld by the scanner. A candidate is only a *candidate*: it triggers no action, and the
deterministic policy/suppression gates still apply downstream. Limitation: obfuscated addresses
("name [at] example [dot] com") and addresses rendered by JavaScript are not extracted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit
from uuid import UUID

from kb_contracts.agents import ContactCandidate, ContactCandidates
from kb_contracts.providers import FetchedPage

from kb_core.discovery import InvalidUrl, canonicalize_url, registered_domain
from kb_core.research.htmlscan import scan_html
from kb_core.research.untrusted import content_hash, normalise_space

CHANNEL_EMAIL = "email"
CHANNEL_CONTACT_PAGE = "contact_page"

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_CONTACT_PATH = re.compile(r"(^|/)(contact|contact[-_ ]us|get[-_ ]in[-_ ]touch)(/|$|\.)", re.I)
_FREEMAIL = frozenset(
    {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "icloud.com"}
)
_ROLE_LOCALS = {
    "editor": "editor",
    "editorial": "editor",
    "editorial-desk": "editor",
    "desk": "editor",
    "submissions": "editor",
    "pitch": "editor",
    "partnerships": "partnerships",
    "press": "press",
    "info": "general",
    "contact": "general",
    "hello": "general",
}
_UNUSABLE_LOCALS = frozenset({"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"})

CONF_MAILTO = 0.9
CONF_TEXT = 0.7
CONF_CONTACT_PAGE = 0.5
SAME_DOMAIN_BONUS = 0.05
FREEMAIL_PENALTY = 0.2


@dataclass(frozen=True)
class ContactEvidence:
    source_url: str
    excerpt: str
    content_hash: str
    kind: str = "contact_channel"


@dataclass(frozen=True)
class ExtractedContact:
    channel_type: str
    value: str  # already lower(btrim(...))
    confidence: float
    role: str | None
    evidence: ContactEvidence


def normalise_value(raw: str) -> str:
    """``lower(btrim(raw))`` as required by the DB constraints."""
    return raw.strip().lower()


def _email_candidates(raw: str) -> list[str]:
    return [m.group(0).rstrip(".") for m in _EMAIL.finditer(raw)]


def _mailto_emails(href: str) -> list[str]:
    if not href.lower().startswith("mailto:"):
        return []
    target = unquote(href[len("mailto:") :].split("?", 1)[0])
    return _email_candidates(target)


def _score_email(email: str, page_domain: str, base: float) -> float:
    domain = email.rsplit("@", 1)[1]
    score = base
    if registered_domain(domain) == page_domain:
        score += SAME_DOMAIN_BONUS
    if domain in _FREEMAIL:
        score -= FREEMAIL_PENALTY
    return round(min(1.0, max(0.0, score)), 4)


def extract_contacts(page: FetchedPage) -> list[ExtractedContact]:
    """Return deduplicated contact candidates, sorted by (-confidence, channel, value)."""
    if not page.robots_allowed or not 200 <= page.status < 300 or not page.html.strip():
        return []
    url = page.final_url or page.url
    try:
        page_domain = canonicalize_url(url).registered_domain
    except InvalidUrl:
        return []
    scanned = scan_html(page.html)
    best: dict[tuple[str, str], ExtractedContact] = {}

    def offer(channel: str, value: str, confidence: float, role: str | None, excerpt: str) -> None:
        text = normalise_space(excerpt)
        contact = ExtractedContact(
            channel_type=channel,
            value=value,
            confidence=confidence,
            role=role,
            evidence=ContactEvidence(
                source_url=url, excerpt=text[:300], content_hash=content_hash(text[:300])
            ),
        )
        key = (channel, value)
        held = best.get(key)
        if held is None or contact.confidence > held.confidence:
            best[key] = contact

    def offer_email(raw: str, base: float, excerpt: str) -> None:
        value = normalise_value(raw)
        local = value.split("@", 1)[0]
        if local in _UNUSABLE_LOCALS:
            return
        offer(
            CHANNEL_EMAIL,
            value,
            _score_email(value, page_domain, base),
            _ROLE_LOCALS.get(local),
            excerpt,
        )

    for link in scanned.links:
        if link.in_quote:
            continue
        for email in _mailto_emails(link.href):
            offer_email(email, CONF_MAILTO, link.text or email)
        href_path = urlsplit(link.href).path
        if (
            link.href
            and not link.href.lower().startswith(("mailto:", "tel:", "javascript:", "#"))
            and (_CONTACT_PATH.search(href_path) or _CONTACT_PATH.search(f"/{link.text}"))
        ):
            absolute = urljoin(url, link.href)
            try:
                target = canonicalize_url(absolute)
            except InvalidUrl:
                continue
            if target.registered_domain == page_domain:  # same-site channels only
                offer(
                    CHANNEL_CONTACT_PAGE,
                    normalise_value(target.canonical),
                    CONF_CONTACT_PAGE,
                    None,
                    link.text or target.canonical,
                )

    for node in scanned.text_nodes:
        if node.in_quote:
            continue
        for email in _email_candidates(node.text):
            offer_email(email, CONF_TEXT, node.text)

    return sorted(best.values(), key=lambda c: (-c.confidence, c.channel_type, c.value))


def to_contact_candidates(
    prospect_id: UUID,
    contacts: list[ExtractedContact],
    evidence_ids: dict[str, UUID],
) -> ContactCandidates:
    """Map extracted contacts onto the frozen ``ContactCandidates`` shape.

    ``evidence_ids`` maps ``content_hash`` -> persisted ``EvidenceItem.id``; a contact whose
    evidence has not been persisted carries no ``evidence_ids`` rather than an invented one.
    """
    candidates = tuple(
        ContactCandidate(
            role=c.role,
            channel_type=c.channel_type,
            value=c.value,
            confidence=c.confidence,
            evidence_ids=(evidence_ids[c.evidence.content_hash],)
            if c.evidence.content_hash in evidence_ids
            else (),
        )
        for c in contacts
    )
    return ContactCandidates(prospect_id=prospect_id, candidates=candidates)
