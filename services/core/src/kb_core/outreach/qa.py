"""Deterministic message QA (docs/13 2b: DRAFT -> QA_PASSED | QA_FAILED).

`run_qa` is a pure function over already-loaded facts, so every rule is unit-testable without a
database. It never trusts the draft: it re-derives, from persisted evidence, whether each claim is
supported.

Checks (all always evaluated, so the operator sees every failure at once):
  EMPTY_CONTENT       subject or body is blank, or the body exceeds `max_body_chars`.
  UNSUPPORTED_CLAIM   a claim cites no evidence id that exists, or cites evidence belonging to a
                      different prospect. (`DraftClaim` already forbids an empty list; existence
                      and ownership are what only the database can prove.)
  CLAIM_NOT_IN_BODY   a declared claim's text does not appear in the body, so the claim list
                      would misrepresent what is actually being sent.
  DUPLICATE_CONTENT   body similarity to a message already sent to the same domain is at or
                      above `similarity_threshold`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from kb_contracts.agents import DraftMessage
from kb_contracts.domain import EvidenceItem, OutreachMessage
from kb_contracts.policy import BlockCode
from pydantic import BaseModel, ConfigDict

from kb_core.outreach.ports import QA_VERSION
from kb_core.outreach.similarity import similarity

DEFAULT_SIMILARITY_THRESHOLD = 0.8
DEFAULT_MAX_BODY_CHARS = 5000


class QaCheck(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    passed: bool
    detail: str = ""


class QaResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    passed: bool
    qa_version: str
    checks: tuple[QaCheck, ...]
    reason_codes: tuple[str, ...]
    max_similarity: float
    similar_to_message_id: UUID | None = None
    unsupported_claim_indexes: tuple[int, ...] = ()

    def as_record(self, provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = self.model_dump(mode="json")
        record["provenance"] = dict(provenance or {})
        return record


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def run_qa(
    draft: DraftMessage,
    *,
    prospect_id: UUID,
    evidence: Sequence[EvidenceItem],
    previous_sent: Sequence[OutreachMessage],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> QaResult:
    by_id = {e.id: e for e in evidence}
    checks: list[QaCheck] = []
    codes: list[str] = []

    blank = not draft.subject.strip() or not draft.body.strip()
    too_long = len(draft.body) > max_body_chars
    checks.append(
        QaCheck(
            name="content_present",
            passed=not (blank or too_long),
            detail="blank subject/body" if blank else ("body too long" if too_long else ""),
        )
    )
    if blank or too_long:
        codes.append("EMPTY_CONTENT")

    bad: list[int] = []
    for i, claim in enumerate(draft.claims):
        if any(
            eid not in by_id or by_id[eid].prospect_id != prospect_id for eid in claim.evidence_ids
        ):
            bad.append(i)
    checks.append(
        QaCheck(
            name="claims_supported",
            passed=not bad,
            detail=f"unsupported claim indexes {bad}" if bad else "",
        )
    )
    if bad:
        codes.append(BlockCode.UNSUPPORTED_CLAIM.value)

    body = _normalise(draft.body)
    missing = [i for i, c in enumerate(draft.claims) if _normalise(c.text) not in body]
    checks.append(
        QaCheck(
            name="claims_in_body",
            passed=not missing,
            detail=f"claim indexes absent from body {missing}" if missing else "",
        )
    )
    if missing:
        codes.append("CLAIM_NOT_IN_BODY")

    best, best_id = 0.0, None
    for prior in previous_sent:
        score = similarity(draft.body, prior.body)
        if score > best:
            best, best_id = score, prior.id
    duplicate = best >= similarity_threshold
    checks.append(
        QaCheck(
            name="not_duplicate",
            passed=not duplicate,
            detail=f"similarity {best:.2f} >= {similarity_threshold:.2f}" if duplicate else "",
        )
    )
    if duplicate:
        codes.append("DUPLICATE_CONTENT")

    return QaResult(
        passed=not codes,
        qa_version=QA_VERSION,
        checks=tuple(checks),
        reason_codes=tuple(dict.fromkeys(codes)),
        max_similarity=round(best, 4),
        similar_to_message_id=best_id if duplicate else None,
        unsupported_claim_indexes=tuple(bad),
    )
