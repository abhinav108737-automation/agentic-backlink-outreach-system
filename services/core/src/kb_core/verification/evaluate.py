"""Pure backlink checks: target URL, anchor text, `rel`, HTTP status, canonical URL.

Outcomes:
  VERIFIED      status 2xx, the target is linked, the anchor matches (when one was agreed), no
                `nofollow` / `sponsored` / `ugc`, and the page's canonical/final URL is the page
                we were told the link lives on.
  CHANGED       the target is linked but something above differs. Reasons are listed.
  MISSING       the link is absent, or the page is gone (404 / 410).
  INCONCLUSIVE  we could not tell (robots disallow, 5xx / 429 / 401 / 403, provider failure).
                This is never counted as a miss, so a flaky host cannot push a good link to LOST.

Only VERIFIED is an active backlink anywhere in the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kb_contracts.domain import Backlink
from kb_contracts.enums import BacklinkStatus
from kb_contracts.providers import FetchedPage

from kb_core.discovery.canonical import InvalidUrl, canonicalize_url
from kb_core.verification.page import FoundLink, analyse_page

NON_FOLLOW_RELS = frozenset({"nofollow", "sponsored", "ugc"})
GONE_STATUSES = frozenset({404, 410})
ACTIVE_BACKLINK_STATUSES = frozenset({BacklinkStatus.VERIFIED})


class Outcome(StrEnum):
    VERIFIED = "VERIFIED"
    CHANGED = "CHANGED"
    MISSING = "MISSING"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class CheckResult:
    outcome: Outcome
    reasons: tuple[str, ...]
    link: FoundLink | None = None
    http_status: int | None = None
    final_url: str | None = None
    canonical_url: str | None = None


def is_active(backlink: Backlink) -> bool:
    """The single definition of an active backlink: VERIFIED, nothing else."""
    return backlink.status in ACTIVE_BACKLINK_STATUSES


def count_active(backlinks: list[Backlink]) -> int:
    return sum(1 for b in backlinks if is_active(b))


def _canon(url: str) -> str | None:
    try:
        return canonicalize_url(url).canonical
    except InvalidUrl:
        return None


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def evaluate(
    page: FetchedPage | None, backlink: Backlink, *, fetch_error: str | None = None
) -> CheckResult:
    if page is None:
        return CheckResult(Outcome.INCONCLUSIVE, (fetch_error or "fetch_failed",))

    def result(
        outcome: Outcome,
        reasons: tuple[str, ...],
        link: FoundLink | None = None,
        canonical: str | None = None,
    ) -> CheckResult:
        return CheckResult(outcome, reasons, link, page.status, page.final_url, canonical)

    if not page.robots_allowed:
        return result(Outcome.INCONCLUSIVE, ("robots_disallowed",))
    if page.status in GONE_STATUSES:
        return result(Outcome.MISSING, (f"http_{page.status}",))
    if not 200 <= page.status < 300:
        return result(Outcome.INCONCLUSIVE, (f"http_{page.status}",))

    facts = analyse_page(page.html)
    canonical_href = _canon(facts.canonical_href) if facts.canonical_href else None
    target = _canon(backlink.target_url)
    matches = [link for link in facts.links if _canon(link.href) == target]
    if not matches:
        return result(Outcome.MISSING, ("link_absent",), canonical=canonical_href)

    source = _canon(backlink.source_url)
    page_problems: list[str] = []
    if canonical_href is not None and canonical_href != source:
        page_problems.append("canonical_mismatch")
    final = _canon(page.final_url)
    if final is not None and final != source:
        page_problems.append("redirected")

    best: tuple[tuple[str, ...], FoundLink] | None = None
    for link in matches:
        found = list(page_problems)
        if backlink.expected_anchor and _norm(link.anchor_text) != _norm(backlink.expected_anchor):
            found.append("anchor_mismatch")
        bad_rel = NON_FOLLOW_RELS & set(link.rel)
        if bad_rel:
            found.append("rel_" + "_".join(sorted(bad_rel)))
        if best is None or len(found) < len(best[0]):
            best = (tuple(found), link)
    assert best is not None
    reasons, link = best
    return result(Outcome.CHANGED if reasons else Outcome.VERIFIED, reasons, link, canonical_href)
