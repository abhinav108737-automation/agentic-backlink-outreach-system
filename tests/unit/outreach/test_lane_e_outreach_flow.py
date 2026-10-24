"""Draft -> QA -> approval -> mock send -> follow-up, over real PostgreSQL and Lane B's services.

No network: the only provider is `MockEmailProvider`. Every message is mock-delivered.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from flowkit import (
    CORR,
    World,
    approved_and_queued,
    draft_step,
    job,
    messages,
    send_handler,
    send_job,
)
from kb_contracts.agents import DraftClaim, DraftMessage
from kb_contracts.domain import OutreachMessage
from kb_contracts.enums import (
    CampaignStatus,
    MessageStatus,
    ProspectStatus,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.providers import (
    AuthRequired,
    EmailProvider,
    OutboundEmail,
    ProviderHealth,
    ProviderUnavailable,
    SendReceipt,
)
from kb_core.audit.service import OPERATOR
from kb_core.errors import GuardFailed, InvalidInput
from kb_core.outreach.context import DraftContext
from kb_core.outreach.handlers import make_draft_handler, make_follow_up_handler
from kb_core.outreach.send import SendDeferred
from kb_core.suppression.service import SuppressionService
from kb_providers.email import MockEmailProvider

M = MessageStatus


def producer_with(body: str, claim_text: str | None = None, evidence_id=None):
    async def produce(ctx: DraftContext) -> DraftMessage:
        claims = ()
        if claim_text is not None:
            claims = (DraftClaim(text=claim_text, evidence_ids=(evidence_id or uuid4(),)),)
        return DraftMessage(
            sequence_id=ctx.sequence.id,
            step_no=ctx.step_no,
            subject="Guest article idea",
            body=body,
            claims=claims,
        )

    return produce


async def run_draft(world: World, produce, *, sequence_id=None, step_no: int = 1) -> None:
    handler = make_draft_handler(world.factory, world.transitions, produce)
    await handler(
        job(
            "outreach.draft",
            {"sequence_id": str(sequence_id or world.sequence.id), "step_no": step_no},
        )
    )


async def jobs_of(world: World, job_type: str):
    return await world.sql(
        "select status, payload, subject_type, subject_id from scheduled_jobs"
        " where job_type = :t order by created_at",
        t=job_type,
    )


# ============================================================ drafts, evidence and QA
async def test_draft_is_evidenced_qa_recorded_and_routed_for_approval(world: World) -> None:
    await draft_step(world)
    (m,) = await messages(world)
    assert m.status is M.PENDING_APPROVAL and m.message_version == 1
    assert m.idempotency_key == f"msg:{world.sequence.id}:1:1"
    cited = {e for ids in m.claims.values() for e in ids}
    assert cited and cited <= {e.id for e in world.evidence}  # claims map to real evidence

    (row,) = await world.sql("select qa_result, claims from outreach_messages")
    qa = row["qa_result"]
    assert qa["passed"] is True and qa["reason_codes"] == []
    assert qa["qa_version"] and qa["provenance"]["template_version"] == "tmpl-guestpost-1"
    assert {c["name"] for c in qa["checks"]} == {
        "content_present",
        "claims_supported",
        "claims_in_body",
        "not_duplicate",
    }
    (approval,) = await world.sql(
        "select kind, status, subject_type, subject_id from approval_requests"
    )
    assert (approval["kind"], approval["status"]) == ("message_send", "PENDING")
    assert (approval["subject_type"], approval["subject_id"]) == ("message", m.id)
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.status is SequenceStatus.READY
        actions = [a["action"] for a in await uow.audit.list_for_subject("outreach_message", m.id)]
    assert actions.count("outreach_message.transition") == 2  # DRAFT->QA_PASSED->PENDING_APPROVAL


async def test_a_redelivered_draft_job_creates_no_second_message(world: World) -> None:
    await draft_step(world)
    await draft_step(world)
    assert len(await messages(world)) == 1
    assert len(await world.sql("select 1 from approval_requests")) == 1


async def test_claim_whose_evidence_does_not_exist_is_blocked_by_qa(world: World) -> None:
    body = "Hello. We saw your award win last year. Regards"
    await run_draft(world, producer_with(body, "We saw your award win last year.", uuid4()))
    (m,) = await messages(world)
    assert m.status is M.QA_FAILED
    (row,) = await world.sql("select qa_result from outreach_messages")
    assert "UNSUPPORTED_CLAIM" in row["qa_result"]["reason_codes"]
    assert row["qa_result"]["passed"] is False
    assert await world.sql("select 1 from approval_requests") == []  # nothing is sent for approval
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.status is SequenceStatus.PLANNED


async def test_claim_citing_another_prospects_evidence_is_blocked(world: World) -> None:
    _, _, other_evidence = await world.new_prospect_sequence(
        "other-site.example.org", "e@other-site.example.org"
    )
    text = "We read your coverage of devices."
    await run_draft(world, producer_with(f"Hello. {text} Regards", text, other_evidence[0].id))
    (m,) = await messages(world)
    assert m.status is M.QA_FAILED


async def test_the_transition_guard_independently_refuses_unsupported_claims(world: World) -> None:
    """Defence in depth: even a message that skipped QA cannot become QA_PASSED."""
    async with world.factory() as uow:
        draft = await uow.messages.create(
            sequence_id=world.sequence.id,
            step_no=1,
            subject="s",
            body="b",
            idempotency_key="guard-test",
            claims={"never evidenced": [str(uuid4())]},
        )
    with pytest.raises(GuardFailed) as exc:
        await world.transitions.transition(
            draft, M.QA_PASSED, actor=OPERATOR, reason="try", correlation_id=CORR
        )
    assert exc.value.code == "UNSUPPORTED_CLAIM"


async def test_failed_qa_is_revised_as_a_new_version_and_revisions_are_bounded(
    world: World,
) -> None:
    bad = producer_with("Hello. Unfounded claim here. Regards", "Unfounded claim here.")
    await run_draft(world, bad)
    await draft_step(world)  # the good template draft is version 2
    versions = [(m.message_version, m.status) for m in await messages(world)]
    assert versions == [(1, M.QA_FAILED), (2, M.PENDING_APPROVAL)]

    other_world_seq = (
        await world.new_prospect_sequence("third-site.example.net", "e@third-site.example.net")
    )[1]
    for _ in range(3):
        await run_draft(world, bad, sequence_id=other_world_seq.id)
    with pytest.raises(InvalidInput, match="failed QA 3 times"):
        await run_draft(world, bad, sequence_id=other_world_seq.id)


async def test_near_duplicate_of_a_message_sent_to_the_same_domain_is_rejected(
    world: World,
) -> None:
    sent = await approved_and_queued(world)
    _, handler = send_handler(world)
    await handler(send_job(sent))
    assert (await messages(world))[0].status is M.SENT

    async with world.factory() as uow:  # a second campaign targets the same registered domain
        c2 = await uow.campaigns.create(name="second")
    _, seq2, _ = await _seed(world, c2)
    await run_draft(world, producer_with(sent.body), sequence_id=seq2.id)
    (dup,) = await messages(world, seq2.id)
    assert dup.status is M.QA_FAILED
    (row,) = await world.sql("select qa_result from outreach_messages where id = :i", i=dup.id)
    assert row["qa_result"]["reason_codes"] == ["DUPLICATE_CONTENT"]
    assert row["qa_result"]["similar_to_message_id"] == str(sent.id)

    await run_draft(
        world,
        producer_with("A wholly different note about gardening supplies."),
        sequence_id=seq2.id,
    )
    assert [m.status for m in await messages(world, seq2.id)] == [M.QA_FAILED, M.PENDING_APPROVAL]


async def _seed(world: World, campaign):
    from flowkit import seed_prospect

    return await seed_prospect(
        world.factory, campaign, world.domain_name, "second@editorial-desk.example.com"
    )


# ============================================================ mock sending
async def test_approved_message_is_delivered_once_in_mock_mode(world: World) -> None:
    m = await approved_and_queued(world)
    assert m.status is M.QUEUED and len(await jobs_of(world, "outreach.send")) == 1
    provider, handler = send_handler(world)

    await handler(send_job(m))
    (sent,) = await messages(world)
    assert sent.status is M.SENT and sent.delivery_mode.value == "mock"
    (row,) = await world.sql("select provider_message_id, attempts from outreach_messages")
    assert row["provider_message_id"] == provider.deliveries[0].receipt.provider_message_id
    assert provider.deliveries[0].receipt.delivery_mode == "mock"
    assert provider.deliveries[0].message.to == world.channel_email

    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        prospect = await uow.prospects.get(world.prospect.id)
        assert seq is not None and seq.status is SequenceStatus.WAITING_REPLY
        assert prospect is not None and prospect.status is ProspectStatus.IN_OUTREACH
    (follow,) = await jobs_of(world, "outreach.follow_up")
    assert follow["subject_type"] == "sequence" and follow["subject_id"] == world.sequence.id
    assert follow["payload"]["step_no"] == 2


async def test_redelivering_the_send_job_never_delivers_twice(world: World) -> None:
    m = await approved_and_queued(world)
    provider, handler = send_handler(world)
    for _ in range(3):
        await handler(send_job(m))
    assert len(provider.deliveries) == 1
    assert len(await jobs_of(world, "outreach.follow_up")) == 1


async def test_crash_after_sending_reconciles_with_the_provider_before_any_retry(
    world: World,
) -> None:
    m = await approved_and_queued(world)
    await world.gate.begin_send(m.id, correlation_id=CORR)  # row is SENDING; worker "crashes"
    provider, handler = send_handler(world)
    # The provider had already accepted the message before the crash.
    await provider.send(
        OutboundEmail(
            idempotency_key=m.idempotency_key,
            to=world.channel_email,
            subject=m.subject,
            body=m.body,
        )
    )
    await handler(send_job(m))
    assert len(provider.deliveries) == 1  # reconciled, not resent
    assert (await messages(world))[0].status is M.SENT


async def test_crash_before_the_provider_saw_it_sends_exactly_once(world: World) -> None:
    m = await approved_and_queued(world)
    await world.gate.begin_send(m.id, correlation_id=CORR)
    provider, handler = send_handler(world)
    await handler(send_job(m))
    await handler(send_job(m))
    assert len(provider.deliveries) == 1 and (await messages(world))[0].status is M.SENT


class _NoLookup:
    """A provider that cannot answer 'was this key sent?': reconcile must lean on C5 idempotency."""

    name, is_paid = "mock_email", False

    def __init__(self, inner: MockEmailProvider) -> None:
        self.inner = inner

    async def health(self) -> ProviderHealth:
        return await self.inner.health()

    async def send(self, message: OutboundEmail) -> SendReceipt:
        return await self.inner.send(message)

    async def fetch_replies(self, since=None):
        return await self.inner.fetch_replies(since)


async def test_reconcile_without_a_lookup_capability_still_cannot_double_send(world: World) -> None:
    from kb_core.outreach.handlers import make_send_handler

    m = await approved_and_queued(world)
    await world.gate.begin_send(m.id, correlation_id=CORR)
    inner = MockEmailProvider(clock=world.clock)
    await inner.send(
        OutboundEmail(
            idempotency_key=m.idempotency_key,
            to=world.channel_email,
            subject=m.subject,
            body=m.body,
        )
    )
    wrapped: EmailProvider = _NoLookup(inner)  # type: ignore[assignment]
    handler = make_send_handler(world.factory, wrapped, world.gate, world.transitions, world.clock)
    await handler(send_job(m))
    assert len(inner.deliveries) == 1 and (await messages(world))[0].status is M.SENT


async def test_retryable_provider_error_retries_then_succeeds_once(world: World) -> None:
    m = await approved_and_queued(world)
    provider = MockEmailProvider(clock=world.clock, simulate=ProviderUnavailable, simulate_times=1)
    _, handler = send_handler(world, provider)
    with pytest.raises(ProviderUnavailable):
        await handler(send_job(m))
    assert (await messages(world))[0].status is M.RETRY_WAIT and provider.deliveries == ()
    await handler(send_job(m))
    assert (await messages(world))[0].status is M.SENT and len(provider.deliveries) == 1


async def test_retries_are_bounded_then_the_message_fails(world: World) -> None:
    m = await approved_and_queued(world)
    provider = MockEmailProvider(clock=world.clock, simulate=ProviderUnavailable)
    _, handler = send_handler(world, provider)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await handler(send_job(m))
    await handler(send_job(m))  # third attempt exhausts the budget: FAILED, no raise
    assert (await messages(world))[0].status is M.FAILED and provider.deliveries == ()
    await handler(send_job(m))  # a terminal message is never retried
    assert provider.deliveries == ()


async def test_auth_error_fails_immediately(world: World) -> None:
    m = await approved_and_queued(world)
    provider = MockEmailProvider(clock=world.clock, simulate=AuthRequired)
    _, handler = send_handler(world, provider)
    await handler(send_job(m))
    assert (await messages(world))[0].status is M.FAILED


async def test_suppression_after_approval_stops_the_send(world: World) -> None:
    m = await approved_and_queued(world)
    async with world.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=world.channel_email,
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id=CORR,
        )
    provider, handler = send_handler(world)
    await handler(send_job(m))
    assert provider.deliveries == () and (await messages(world))[0].status is M.POLICY_BLOCKED


async def test_no_sender_is_invented_a_missing_sender_defers_the_send(world: World) -> None:
    m = await approved_and_queued(world)
    await world.sql("update campaigns set sender_provider_account_id = null")
    provider, handler = send_handler(world)
    with pytest.raises(SendDeferred) as exc:
        await handler(send_job(m))
    assert "SENDER_INVALID" in exc.value.codes
    assert provider.deliveries == () and (await messages(world))[0].status is M.QUEUED


async def test_paused_campaign_defers_and_kill_switch_style_blocks_never_send(world: World) -> None:
    m = await approved_and_queued(world)
    async with world.factory() as uow:
        c = await uow.campaigns.get(world.campaign.id)
        assert c is not None
        await uow.campaigns.set_status(
            c.id, CampaignStatus.PAUSED.value, expected_version=c.version
        )
    provider, handler = send_handler(world)
    with pytest.raises(SendDeferred):
        await handler(send_job(m))
    assert provider.deliveries == ()


async def test_real_delivery_mode_is_never_reachable_with_real_send_off(world: World) -> None:
    m = await approved_and_queued(world)
    await world.sql("update outreach_messages set delivery_mode = 'real'")
    provider, handler = send_handler(world)
    with pytest.raises(SendDeferred) as exc:
        await handler(send_job(m))
    assert "REAL_SEND_DISABLED" in exc.value.codes and provider.deliveries == ()


# ============================================================ follow-ups
async def _sent_sequence(world: World) -> OutreachMessage:
    m = await approved_and_queued(world)
    _, handler = send_handler(world)
    await handler(send_job(m))
    return m


def _follow_up(world: World):
    return make_follow_up_handler(world.factory, world.transitions, world.policy, world.clock)


async def test_follow_up_timer_drafts_the_next_step(world: World) -> None:
    await _sent_sequence(world)
    await _follow_up(world)(
        job("outreach.follow_up", {"sequence_id": str(world.sequence.id), "step_no": 2})
    )
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.status is SequenceStatus.FOLLOW_UP_DUE
    (draft_job,) = await jobs_of(world, "outreach.draft")
    assert draft_job["payload"]["step_no"] == 2 and draft_job["subject_type"] == "sequence"

    await draft_step(world, step_no=2)  # the follow-up draft is its own evidenced, QA'd message
    step2 = [m for m in await messages(world) if m.step_no == 2]
    assert [m.status for m in step2] == [M.PENDING_APPROVAL]


async def test_exhausted_steps_complete_the_sequence_without_a_reply(world: World) -> None:
    await _sent_sequence(world)
    await _follow_up(world)(
        job("outreach.follow_up", {"sequence_id": str(world.sequence.id), "step_no": 4})
    )  # default policy allows 2 follow-ups: steps 1..3
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.status is SequenceStatus.COMPLETED_NO_REPLY
    assert await jobs_of(world, "outreach.draft") == []


async def test_follow_up_after_a_reply_is_a_no_op(world: World) -> None:
    await _sent_sequence(world)
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None
        await world.transitions.transition_in(
            uow, seq, SequenceStatus.REPLIED, actor=OPERATOR, reason="reply", correlation_id=CORR
        )
    await _follow_up(world)(
        job("outreach.follow_up", {"sequence_id": str(world.sequence.id), "step_no": 2})
    )
    assert await jobs_of(world, "outreach.draft") == []


async def test_follow_up_to_a_suppressed_contact_cancels_the_sequence(world: World) -> None:
    await _sent_sequence(world)
    async with world.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value=world.channel_email,
            reason=SuppressionReason.MANUAL,
            actor=OPERATOR,
            correlation_id=CORR,
        )
    await _follow_up(world)(
        job("outreach.follow_up", {"sequence_id": str(world.sequence.id), "step_no": 2})
    )
    async with world.factory() as uow:
        seq = await uow.sequences.get(world.sequence.id)
        assert seq is not None and seq.status is SequenceStatus.CANCELLED
    assert await jobs_of(world, "outreach.draft") == []
