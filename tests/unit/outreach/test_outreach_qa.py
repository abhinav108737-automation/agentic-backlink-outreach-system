"""Pure QA rules: claim evidence existence/ownership, claim-in-body, duplicates, templates."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from kb_contracts.agents import DraftClaim, DraftMessage
from kb_contracts.domain import EvidenceItem, OutreachMessage
from kb_contracts.enums import MessageStatus
from kb_core.outreach.qa import run_qa
from kb_core.outreach.similarity import similarity
from pydantic import ValidationError

NOW = datetime(2026, 3, 1, tzinfo=UTC)
PROSPECT = uuid4()
BODY = "Hello. We liked your piece on devices. Regards"


def evidence(prospect=PROSPECT) -> EvidenceItem:
    return EvidenceItem(
        id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
        prospect_id=prospect,
        source_url="https://x.example.test/a",
        excerpt="e",
        content_hash="h",
        kind="topic",
        extracted_at=NOW,
    )


def draft(*claims: DraftClaim, body: str = BODY) -> DraftMessage:
    return DraftMessage(sequence_id=uuid4(), step_no=1, subject="Idea", body=body, claims=claims)


def sent(body: str) -> OutreachMessage:
    return OutreachMessage(
        id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
        version=1,
        sequence_id=uuid4(),
        step_no=1,
        message_version=1,
        status=MessageStatus.SENT,
        subject="s",
        body=body,
        idempotency_key=str(uuid4()),
    )


def qa(d: DraftMessage, **kw):
    kw.setdefault("evidence", [])
    kw.setdefault("previous_sent", [])
    return run_qa(d, prospect_id=PROSPECT, **kw)


def test_an_unsupported_claim_is_unrepresentable_without_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        DraftClaim(text="We liked your piece on devices", evidence_ids=())


def test_supported_claim_passes() -> None:
    e = evidence()
    claim = DraftClaim(text="We liked your piece on devices", evidence_ids=(e.id,))
    result = qa(draft(claim), evidence=[e])
    assert result.passed and result.reason_codes == () and result.qa_version


def test_claim_citing_evidence_that_does_not_exist_fails_qa() -> None:
    claim = DraftClaim(text="We liked your piece on devices", evidence_ids=(uuid4(),))
    result = qa(draft(claim))
    assert not result.passed
    assert "UNSUPPORTED_CLAIM" in result.reason_codes
    assert result.unsupported_claim_indexes == (0,)


def test_one_real_and_one_missing_evidence_id_still_fails() -> None:
    e = evidence()
    claim = DraftClaim(text="We liked your piece on devices", evidence_ids=(e.id, uuid4()))
    assert "UNSUPPORTED_CLAIM" in qa(draft(claim), evidence=[e]).reason_codes


def test_evidence_belonging_to_another_prospect_does_not_support_a_claim() -> None:
    other = evidence(prospect=uuid4())
    claim = DraftClaim(text="We liked your piece on devices", evidence_ids=(other.id,))
    assert "UNSUPPORTED_CLAIM" in qa(draft(claim), evidence=[other]).reason_codes


def test_declared_claim_absent_from_the_body_fails() -> None:
    e = evidence()
    claim = DraftClaim(text="You won an award last year", evidence_ids=(e.id,))
    assert qa(draft(claim), evidence=[e]).reason_codes == ("CLAIM_NOT_IN_BODY",)


def test_near_duplicate_of_a_message_already_sent_to_the_domain_fails() -> None:
    body = "Hello there. We would like to propose an original guest article about devices today."
    result = qa(draft(body=body), previous_sent=[sent(body)])
    assert not result.passed and result.reason_codes == ("DUPLICATE_CONTENT",)
    assert result.max_similarity == 1.0 and result.similar_to_message_id is not None


def test_a_different_message_passes_the_similarity_check() -> None:
    other = sent("Completely unrelated wording about gardening, soil and seasonal planting tips.")
    result = qa(draft(), previous_sent=[other])
    assert result.passed and result.max_similarity < 0.8


def test_blank_and_oversized_content_fail() -> None:
    assert "EMPTY_CONTENT" in qa(draft(body="   ")).reason_codes
    assert "EMPTY_CONTENT" in qa(draft(body="word " * 2000)).reason_codes


def test_all_failures_are_reported_together_and_recorded() -> None:
    claim = DraftClaim(text="never said", evidence_ids=(uuid4(),))
    result = qa(draft(claim))
    assert set(result.reason_codes) == {"UNSUPPORTED_CLAIM", "CLAIM_NOT_IN_BODY"}
    record = result.as_record({"prompt_version": "p1"})
    assert record["passed"] is False and record["provenance"] == {"prompt_version": "p1"}


def test_similarity_is_symmetric_bounded_and_deterministic() -> None:
    a, b = "the quick brown fox jumps over the lazy dog", "a quick brown fox jumps over a dog"
    assert similarity(a, b) == similarity(b, a) and 0 < similarity(a, b) < 1
    assert similarity(a, a) == 1.0 and similarity("", a) == 0.0
