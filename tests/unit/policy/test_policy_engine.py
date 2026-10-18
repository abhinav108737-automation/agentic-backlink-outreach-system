"""Every BlockCode the engine emits, with its triggering condition (and the allow path)."""

from uuid import uuid4

import pytest
from kb_contracts.config import Settings
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import (
    ApprovalKind,
    ApprovalStatus,
    CampaignStatus,
    SuppressionReason,
    SuppressionScope,
)
from kb_contracts.policy import BlockCode, PolicyCheckRequest
from kb_core.audit.service import OPERATOR
from kb_core.errors import EntityNotFound, PolicyUnavailable
from kb_core.qualification.evaluator import METRIC_DA, METRIC_SPAM
from kb_core.settings.service import SettingsService
from kb_core.suppression.service import SuppressionService
from kb_core.testing.fakes import FakeUowFactory
from kb_core.testing.scenario import Scenario


def req(campaign, prospect=None, action="qualify", **ctx):
    return PolicyCheckRequest(
        action=action,
        campaign_id=campaign.id,
        subject_type="prospect" if prospect else "message",
        subject_id=prospect.id if prospect else uuid4(),
        context=ctx,
        correlation_id="c1",
    )


async def test_allowed_when_everything_is_clean():
    sc = Scenario()
    c = await sc.campaign()
    p = await sc.prospect(c)
    await sc.good_metrics(p)
    d = await sc.policy.check(req(c, p))
    assert d.allowed and d.block_codes == () and d.policy_version_id == sc.policy_version_id


async def test_kill_switch_blocks_every_action():
    sc = Scenario()
    c = await sc.campaign()
    await SettingsService(sc.factory).set_kill_switch(
        active=True, reason="stop", actor=OPERATOR, correlation_id="k"
    )
    for action in ("qualify", "enqueue_send", "follow_up"):
        d = await sc.policy.check(req(c, action=action, delivery_mode="mock"))
        assert BlockCode.KILL_SWITCH_ACTIVE in d.block_codes and not d.allowed


async def test_malformed_kill_switch_row_fails_closed():
    sc = Scenario()
    c = await sc.campaign()
    sc.store.settings["kill_switch"] = {"value": {}, "version": 1}
    d = await sc.policy.check(req(c))
    assert BlockCode.KILL_SWITCH_ACTIVE in d.block_codes


async def test_kill_switch_off_does_not_block():
    sc = Scenario()
    c = await sc.campaign()
    svc = SettingsService(sc.factory)
    await svc.set_kill_switch(active=True, reason=None, actor=OPERATOR, correlation_id="k")
    await svc.set_kill_switch(active=False, reason=None, actor=OPERATOR, correlation_id="k")
    assert BlockCode.KILL_SWITCH_ACTIVE not in (await sc.policy.check(req(c))).block_codes


@pytest.mark.parametrize("status", [CampaignStatus.PAUSED, CampaignStatus.ARCHIVED])
async def test_paused_or_archived_campaign_blocks_even_non_outbound(status):
    sc = Scenario()
    c = await sc.campaign(status)
    d = await sc.policy.check(req(c))
    assert BlockCode.CAMPAIGN_PAUSED in d.block_codes
    assert d.detail["campaign_status"] == status.value


async def test_draft_campaign_blocks_outbound_but_not_qualify():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.DRAFT)
    assert BlockCode.CAMPAIGN_PAUSED not in (await sc.policy.check(req(c))).block_codes
    d = await sc.policy.check(req(c, action="follow_up"))
    assert BlockCode.CAMPAIGN_PAUSED in d.block_codes


async def test_suppressed_email_blocks_and_cannot_be_bypassed():
    sc = Scenario()
    c = await sc.campaign()
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="  Editor@Example.com ",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    d = await sc.policy.check(req(c, action="follow_up", email="editor@example.com"))
    assert BlockCode.SUPPRESSED in d.block_codes and not d.allowed
    # no context key, flag or action name lifts it
    for extra in ({"force": True}, {"override": "yes"}, {"skip_suppression": True}):
        d = await sc.policy.check(req(c, action="follow_up", email="EDITOR@example.com", **extra))
        assert BlockCode.SUPPRESSED in d.block_codes


async def test_domain_suppression_covers_email_at_that_domain_and_prospect_subject():
    sc = Scenario()
    c = await sc.campaign()
    p = await sc.prospect(c, "blocked.org")
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.DOMAIN,
            value="https://www.Blocked.org/x",
            reason=SuppressionReason.MANUAL,
            actor=OPERATOR,
            correlation_id="s",
        )
    by_email = await sc.policy.check(req(c, email="a@mail.blocked.org"))
    by_prospect = await sc.policy.check(req(c, p))
    assert BlockCode.SUPPRESSED in by_email.block_codes
    assert BlockCode.SUPPRESSED in by_prospect.block_codes


async def test_campaign_scoped_suppression_only_applies_to_that_campaign():
    sc = Scenario()
    a, b = await sc.campaign(), await sc.campaign()
    async with sc.factory() as uow:
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="x@y.com",
            reason=SuppressionReason.MANUAL,
            actor=OPERATOR,
            correlation_id="s",
            campaign_id=a.id,
        )
    assert BlockCode.SUPPRESSED in (await sc.policy.check(req(a, email="x@y.com"))).block_codes
    assert BlockCode.SUPPRESSED not in (await sc.policy.check(req(b, email="x@y.com"))).block_codes


async def test_superseded_suppression_stops_blocking_and_is_audited():
    sc = Scenario()
    c = await sc.campaign()
    async with sc.factory() as uow:
        e = await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="x@y.com",
            reason=SuppressionReason.MANUAL,
            actor=OPERATOR,
            correlation_id="s",
        )
    async with sc.factory() as uow:
        await SuppressionService().supersede(
            uow, entry_id=e.id, actor=OPERATOR, correlation_id="s2", reason="mistake"
        )
    assert BlockCode.SUPPRESSED not in (await sc.policy.check(req(c, email="x@y.com"))).block_codes
    assert {r["action"] for r in sc.store.audit} >= {"suppression.added", "suppression.superseded"}


async def test_metric_missing_state2_and_state3_block_separately():
    sc = Scenario()
    c = await sc.campaign()
    p2, p3 = await sc.prospect(c, "two.com"), await sc.prospect(c, "three.com")
    await sc.metric(p2, METRIC_DA, None)
    await sc.metric(p2, METRIC_SPAM, 0.1)
    await sc.metric(p3, METRIC_SPAM, 0.1)
    d2, d3 = await sc.policy.check(req(c, p2)), await sc.policy.check(req(c, p3))
    for d in (d2, d3):
        assert d.block_codes == (BlockCode.METRIC_MISSING,)
        assert d.detail["missing_metrics"] == [METRIC_DA]
    assert d2.detail["reason_codes"] != d3.detail["reason_codes"]


async def test_hard_threshold_failed():
    sc = Scenario()
    c = await sc.campaign()
    p = await sc.prospect(c)
    await sc.good_metrics(p, da=30, spam=3)
    d = await sc.policy.check(req(c, p))
    assert d.block_codes == (BlockCode.HARD_THRESHOLD_FAILED,)


async def test_reject_on_missing_policy_still_reports_metric_missing():
    sc = Scenario(PolicyBody(on_missing_metric="reject"))
    c = await sc.campaign()
    p = await sc.prospect(c)
    assert BlockCode.METRIC_MISSING in (await sc.policy.check(req(c, p))).block_codes


async def test_real_send_disabled_by_default_and_when_mode_unstated():
    sc = Scenario(PolicyBody(require_approval_for_send=False))
    c = await sc.campaign()
    for ctx in ({"delivery_mode": "real"}, {}):
        d = await sc.policy.check(req(c, action="send", **ctx))
        assert BlockCode.REAL_SEND_DISABLED in d.block_codes
    ok = await sc.policy.check(req(c, action="send", delivery_mode="mock"))
    assert ok.allowed


async def test_mock_send_requires_message_approval_then_passes_once_approved():
    sc = Scenario()  # require_approval_for_send defaults True
    c = await sc.campaign()
    r = req(c, action="enqueue_send", delivery_mode="mock")
    d = await sc.policy.check(r)
    assert d.block_codes == (BlockCode.APPROVAL_REQUIRED,) and d.requires_approval
    async with sc.factory() as uow:
        a = await uow.approvals.create(
            kind=ApprovalKind.MESSAGE_SEND.value,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            requested_by="operator",
        )
        await uow.approvals.decide(
            a.id, status=ApprovalStatus.APPROVED, decided_by="operator", reason=None
        )
    assert (await sc.policy.check(r)).allowed


async def test_rejected_approval_does_not_unblock():
    sc = Scenario()
    c = await sc.campaign()
    r = req(c, action="send", delivery_mode="mock")
    async with sc.factory() as uow:
        a = await uow.approvals.create(
            kind="message_send",
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            requested_by="operator",
        )
        await uow.approvals.decide(
            a.id, status=ApprovalStatus.REJECTED, decided_by="operator", reason="no"
        )
    assert BlockCode.APPROVAL_REQUIRED in (await sc.policy.check(r)).block_codes


async def test_real_send_enabled_still_needs_live_send_activation_approval():
    settings = Settings(_env_file=None, real_send_enabled=True, autonomy_mode="guarded")
    sc = Scenario(PolicyBody(require_approval_for_send=False), settings=settings)
    c = await sc.campaign()
    r = req(c, action="send", delivery_mode="real")
    d = await sc.policy.check(r)
    assert d.block_codes == (BlockCode.APPROVAL_REQUIRED,)
    assert d.detail["approval_kind"] == "live_send_activation"
    async with sc.factory() as uow:
        a = await uow.approvals.create(
            kind="live_send_activation",
            subject_type="campaign",
            subject_id=c.id,
            requested_by="operator",
        )
        await uow.approvals.decide(
            a.id, status=ApprovalStatus.APPROVED, decided_by="operator", reason=None
        )
    assert (await sc.policy.check(r)).allowed


async def test_negotiation_commitment_requires_approval():
    sc = Scenario()
    c = await sc.campaign()
    d = await sc.policy.check(req(c, action="negotiation_commit"))
    assert d.block_codes == (BlockCode.APPROVAL_REQUIRED,)
    sc2 = Scenario(PolicyBody(require_approval_for_commitments=False))
    c2 = await sc2.campaign()
    assert (await sc2.policy.check(req(c2, action="negotiation_commit"))).allowed


async def test_blocked_outbound_action_is_audited_but_qualify_is_not():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.PAUSED)
    await sc.policy.check(req(c, action="qualify"))
    assert not [r for r in sc.store.audit if r["action"] == "policy.blocked"]
    await sc.policy.check(req(c, action="follow_up"))
    assert [r for r in sc.store.audit if r["action"] == "policy.blocked"]


async def test_multiple_codes_are_all_reported():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.PAUSED)
    d = await sc.policy.check(req(c, action="send"))
    assert {
        BlockCode.CAMPAIGN_PAUSED,
        BlockCode.REAL_SEND_DISABLED,
        BlockCode.APPROVAL_REQUIRED,
    } <= set(d.block_codes)


async def test_campaign_pinned_policy_wins_over_global():
    sc = Scenario()
    strict = sc.store.add_policy(PolicyBody(min_da=80), global_active=False)
    async with sc.factory() as uow:
        c = await uow.campaigns.create(name="pinned", policy_version_id=strict)
        c = await uow.campaigns.set_status(c.id, "ACTIVE", expected_version=c.version)
    p = await sc.prospect(c)
    await sc.good_metrics(p, da=60)
    d = await sc.policy.check(req(c, p))
    assert d.policy_version_id == strict
    assert d.block_codes == (BlockCode.HARD_THRESHOLD_FAILED,)


async def test_missing_campaign_and_missing_policy_fail_closed():
    f = FakeUowFactory()
    from kb_core.policy.engine import DeterministicPolicyEngine

    eng = DeterministicPolicyEngine(f, Settings(_env_file=None))
    bad = PolicyCheckRequest(
        action="qualify",
        campaign_id=uuid4(),
        subject_type="x",
        subject_id=uuid4(),
        correlation_id="c",
    )
    with pytest.raises(EntityNotFound):
        await eng.check(bad)
    async with f() as uow:
        c = await uow.campaigns.create(name="n")
    with pytest.raises(PolicyUnavailable):
        await eng.check(bad.model_copy(update={"campaign_id": c.id}))


async def test_prospect_from_another_campaign_is_rejected():
    from kb_core.errors import InvalidInput

    sc = Scenario()
    a, b = await sc.campaign(), await sc.campaign()
    p = await sc.prospect(a)
    with pytest.raises(InvalidInput):
        await sc.policy.check(req(b, p))
