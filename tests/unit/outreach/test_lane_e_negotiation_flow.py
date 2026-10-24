"""Negotiation rounds, forced human approval, the opportunity lifecycle and the backlink hand-off."""

from __future__ import annotations

from uuid import UUID

import pytest
from flowkit import CORR, World, job
from kb_contracts.agents import NegotiationPlan
from kb_contracts.enums import ApprovalStatus, OpportunityStatus
from kb_contracts.providers import InboundEmail
from kb_contracts.state_machines import InvalidTransition
from kb_core.approvals.service import ApprovalService
from kb_core.audit.service import OPERATOR
from kb_core.errors import DomainConflict, InvalidInput
from kb_core.negotiation.handlers import make_negotiation_handler
from kb_core.negotiation.opportunity import OpportunityService
from kb_core.negotiation.service import NegotiationService, detect_commitment, needs_human
from kb_core.replies.handlers import make_classify_handler
from kb_core.replies.service import ReplyService

OS = OpportunityStatus
TARGET = "https://kenresearch.example.test/reports"


def plan(
    opp: UUID,
    *,
    text: str = "Thanks, we can share an outline for review.",
    risk: str = "low",
    binding: bool = False,
) -> NegotiationPlan:
    return NegotiationPlan(
        opportunity_id=opp,
        draft_response=text,
        recommendation="reply with outline",
        risk_level=risk,
        makes_binding_commitment=binding,
    )


async def new_opportunity(world: World, *, target_url: str | None = TARGET) -> UUID:
    async with world.factory() as uow:
        opp = await uow.opportunities.create(
            prospect_id=world.prospect.id, sequence_id=world.sequence.id, target_url=target_url
        )
        return opp.id


async def status_of(world: World, opp_id: UUID) -> OpportunityStatus:
    async with world.factory() as uow:
        opp = await uow.opportunities.get(opp_id)
        assert opp is not None
        return opp.status


def neg(world: World) -> NegotiationService:
    return NegotiationService(world.factory, world.transitions)


async def decide(world: World, opp_id: UUID, status: ApprovalStatus) -> None:
    (a,) = await world.sql(
        "select id from approval_requests where subject_id = :s and status = 'PENDING'", s=opp_id
    )
    await ApprovalService(world.factory).decide(
        a["id"], status=status, decided_by=OPERATOR, reason="human decision", correlation_id=CORR
    )


# ============================================================ rounds
async def test_low_risk_non_binding_round_keeps_negotiating_without_approval(world: World) -> None:
    opp = await new_opportunity(world)
    r1 = await neg(world).plan_round(plan(opp), correlation_id=CORR)
    r2 = await neg(world).plan_round(plan(opp, risk="medium"), correlation_id=CORR)
    assert r1 is not None and r2 is not None
    assert (r1.route, r1.requires_approval, r1.approval) == ("negotiating", False, None)
    assert await status_of(world, opp) is OS.NEGOTIATING
    rounds = await world.sql(
        "select round_no, risk_level, approval_request_id, draft from negotiations order by round_no"
    )
    assert [r["round_no"] for r in rounds] == [1, 2]
    assert rounds[0]["approval_request_id"] is None and rounds[0]["draft"]
    assert await world.sql("select 1 from approval_requests") == []


async def test_binding_commitment_requires_approval_and_never_self_commits(world: World) -> None:
    opp = await new_opportunity(world)
    res = await neg(world).plan_round(plan(opp, binding=True), correlation_id=CORR)
    assert res is not None and res.requires_approval and res.route == "awaiting_approval"
    assert await status_of(world, opp) is OS.AWAITING_APPROVAL
    (a,) = await world.sql("select kind, status, subject_type, subject_id from approval_requests")
    assert (a["kind"], a["status"], a["subject_type"], a["subject_id"]) == (
        "negotiation_commitment",
        "PENDING",
        "opportunity",
        opp,
    )
    (r,) = await world.sql("select approval_request_id from negotiations")
    assert r["approval_request_id"] == res.approval.id  # type: ignore[union-attr]
    (o,) = await world.sql("select requires_approval from guest_post_opportunities")
    assert o["requires_approval"] is True

    with pytest.raises(DomainConflict) as exc:  # nothing can agree it before a human decides
        await neg(world).resolve_approval(opp, correlation_id=CORR)
    assert exc.value.code == "APPROVAL_PENDING"
    assert await status_of(world, opp) is OS.AWAITING_APPROVAL


async def test_an_agent_cannot_understate_a_commitment(world: World) -> None:
    opp = await new_opportunity(world)
    sneaky = plan(
        opp, text="We agree to pay $300 per post, it is a deal.", risk="low", binding=False
    )
    assert detect_commitment(sneaky.draft_response) and needs_human(sneaky)
    res = await neg(world).plan_round(sneaky, correlation_id=CORR)
    assert res is not None and res.route == "awaiting_approval"
    assert len(await world.sql("select 1 from approval_requests")) == 1


async def test_high_risk_hands_the_conversation_to_a_human(world: World) -> None:
    opp = await new_opportunity(world)
    res = await neg(world).plan_round(plan(opp, risk="high"), correlation_id=CORR)
    assert res is not None and res.route == "escalated_to_human" and res.requires_approval
    assert await status_of(world, opp) is OS.ESCALATED_TO_HUMAN
    assert len(await world.sql("select 1 from approval_requests")) == 1


@pytest.mark.parametrize(
    "p",
    [
        dict(binding=True),
        dict(risk="high"),
        dict(risk="high", binding=True),
        dict(text="We guarantee exclusive placement."),
        dict(text="Payment of 500 USD will be sent."),
    ],
)
async def test_no_plan_ever_reaches_agreed_without_an_approved_decision(world: World, p) -> None:
    opp = await new_opportunity(world)
    await neg(world).plan_round(plan(opp, **p), correlation_id=CORR)
    assert await status_of(world, opp) in (OS.AWAITING_APPROVAL, OS.ESCALATED_TO_HUMAN)
    with pytest.raises(DomainConflict):
        await neg(world).resolve_approval(opp, correlation_id=CORR)
    with pytest.raises(DomainConflict):  # and a second plan cannot jump the queue
        await neg(world).plan_round(plan(opp, binding=True), correlation_id=CORR)
    assert await status_of(world, opp) is not OS.AGREED


async def test_an_approved_decision_agrees_it_and_a_rejection_returns_to_negotiating(
    world: World,
) -> None:
    approve_opp = await new_opportunity(world)
    reject_opp = await new_opportunity(world)
    escalated_opp = await new_opportunity(world)
    for o in (approve_opp, reject_opp):
        await neg(world).plan_round(plan(o, binding=True), correlation_id=CORR)
    await neg(world).plan_round(plan(escalated_opp, risk="high"), correlation_id=CORR)

    await decide(world, approve_opp, ApprovalStatus.APPROVED)
    agreed = await neg(world).resolve_approval(approve_opp, correlation_id=CORR)
    assert agreed.status is OS.AGREED and "approval_request_id" in agreed.agreed_terms

    await decide(world, reject_opp, ApprovalStatus.REJECTED)
    back = await neg(world).resolve_approval(reject_opp, correlation_id=CORR)
    assert back.status is OS.NEGOTIATING

    await decide(world, escalated_opp, ApprovalStatus.APPROVED)
    assert (
        await neg(world).resolve_approval(escalated_opp, correlation_id=CORR)
    ).status is OS.AGREED


async def test_planning_is_idempotent_per_key_and_refused_once_agreed(world: World) -> None:
    opp = await new_opportunity(world)
    first = await neg(world).plan_round(plan(opp), correlation_id=CORR, idempotency_key="reply-1")
    again = await neg(world).plan_round(plan(opp), correlation_id=CORR, idempotency_key="reply-1")
    assert first is not None and again is None
    assert len(await world.sql("select 1 from negotiations")) == 1
    async with world.factory() as uow:  # force AGREED through the real machine for the check
        o = await uow.opportunities.get(opp)
        assert o is not None
        await world.transitions.transition_in(
            uow, o, OS.AGREED, actor=OPERATOR, reason="test", correlation_id=CORR
        )
    with pytest.raises(DomainConflict):
        await neg(world).plan_round(plan(opp), correlation_id=CORR)


async def test_negotiation_job_plans_once_per_reply_and_forces_approval(world: World) -> None:
    replies = ReplyService(world.factory, world.transitions)
    await replies.ingest(
        [
            InboundEmail(
                provider_message_id="pm-neg",
                from_address=world.channel_email,
                received_at=world.clock.now(),
                subject="Re: idea",
                body="We can publish a sponsored post for $300. Please confirm today.",
            )
        ],
        campaign_id=world.campaign.id,
        sequence_id=None,
        correlation_id=CORR,
    )
    (reply,) = await world.sql("select id, sequence_id from replies")
    await make_classify_handler(world.factory, world.transitions)(
        job(
            "replies.classify",
            {"reply_id": str(reply["id"]), "sequence_id": str(reply["sequence_id"])},
        )
    )
    (queued,) = await world.sql(
        "select payload from scheduled_jobs where job_type = 'negotiation.plan'"
    )

    async def producer(opportunity, reply_row, classification):
        assert classification is not None and classification.reply_class.value == "negotiation"
        return plan(opportunity.id, text="Happy to proceed at $300.", binding=True)

    handler = make_negotiation_handler(world.factory, world.transitions, producer)
    for _ in range(2):  # redelivery
        await handler(job("negotiation.plan", queued["payload"]))
    (o,) = await world.sql("select id, status from guest_post_opportunities")
    assert o["status"] == "AWAITING_APPROVAL"
    assert len(await world.sql("select 1 from negotiations")) == 1
    assert len(await world.sql("select 1 from approval_requests")) == 1


# ============================================================ opportunity lifecycle
async def agreed(world: World, **kw) -> UUID:
    opp = await new_opportunity(world, **kw)
    await neg(world).plan_round(plan(opp, binding=True), correlation_id=CORR)
    await decide(world, opp, ApprovalStatus.APPROVED)
    await neg(world).resolve_approval(opp, correlation_id=CORR)
    return opp


def ops(world: World) -> OpportunityService:
    return OpportunityService(world.factory, world.transitions)


async def test_content_flow_to_published_creates_an_expected_backlink(world: World) -> None:
    opp = await agreed(world)
    await ops(world).start_content(opp, correlation_id=CORR)
    await ops(world).submit_content(
        opp, title="Healthcare outlook", content_ref="draft://v1", correlation_id=CORR
    )
    await ops(world).request_revision(opp, correlation_id=CORR)
    await ops(world).submit_content(
        opp, title="Healthcare outlook", content_ref="draft://v2", correlation_id=CORR
    )
    assert await world.sql("select 1 from backlinks") == []  # not before PUBLISHED

    moved, backlink = await ops(world).publish(
        opp,
        published_url="https://publisher-verification-lab.net/good-article",
        expected_anchor="healthcare market research reports",
        correlation_id=CORR,
    )
    assert moved.status is OS.PUBLISHED
    assert backlink.status.value == "EXPECTED" and backlink.consecutive_misses == 0
    assert backlink.opportunity_id == opp
    assert backlink.source_url == "https://publisher-verification-lab.net/good-article"
    assert (
        backlink.target_url == TARGET
        and backlink.expected_anchor == "healthcare market research reports"
    )
    (row,) = await world.sql("select status, rel from backlinks")
    assert row["status"] == "EXPECTED" and row["rel"] == []

    subs = await world.sql(
        "select version_no, content_ref, published_url from content_submissions order by version_no"
    )
    assert [s["version_no"] for s in subs] == [1, 2, 3]
    assert subs[2]["published_url"] == backlink.source_url


async def test_publishing_twice_cannot_create_a_second_backlink(world: World) -> None:
    opp = await agreed(world)
    await ops(world).start_content(opp, correlation_id=CORR)
    await ops(world).submit_content(opp, title="t", content_ref="r", correlation_id=CORR)
    url = "https://publisher-verification-lab.net/good-article"
    await ops(world).publish(opp, published_url=url, correlation_id=CORR)
    with pytest.raises(InvalidTransition):
        await ops(world).publish(opp, published_url=url, correlation_id=CORR)
    assert len(await world.sql("select 1 from backlinks")) == 1


async def test_publish_without_a_target_or_with_a_bad_url_changes_nothing(world: World) -> None:
    opp = await agreed(world, target_url=None)
    await ops(world).start_content(opp, correlation_id=CORR)
    await ops(world).submit_content(opp, title="t", content_ref="r", correlation_id=CORR)
    with pytest.raises(InvalidInput):
        await ops(world).publish(opp, published_url="https://x.example.org/a", correlation_id=CORR)
    with pytest.raises(InvalidInput):
        await ops(world).publish(opp, published_url="not a url", correlation_id=CORR)
    assert await status_of(world, opp) is OS.CONTENT_SUBMITTED
    assert await world.sql("select 1 from backlinks") == []


async def test_illegal_content_moves_are_refused(world: World) -> None:
    opp = await new_opportunity(world)
    with pytest.raises(InvalidTransition):  # OPEN cannot start content work
        await ops(world).start_content(opp, correlation_id=CORR)
    with pytest.raises(InvalidTransition):
        await ops(world).publish(opp, published_url="https://p.example.org/a", correlation_id=CORR)
    lost = await ops(world).mark_lost(opp, reason="declined", correlation_id=CORR)
    assert lost.status is OS.LOST
