"""Exhaustive invalid-transition rejection, guards, optimistic locking and atomic rollback."""

import itertools

import pytest
from kb_contracts.enums import CampaignStatus, ProspectStatus, SuppressionReason, SuppressionScope
from kb_contracts.state_machines import CAMPAIGN, PROSPECT, InvalidTransition
from kb_core.audit.service import OPERATOR
from kb_core.db.protocols import OptimisticLockError
from kb_core.errors import GuardFailed, InvalidInput
from kb_core.qualification.evaluator import METRIC_DA
from kb_core.suppression.service import SuppressionService
from kb_core.testing.scenario import Scenario

P = ProspectStatus
KW = {"actor": OPERATOR, "reason": "test", "correlation_id": "c"}
ILLEGAL_PROSPECT = [(a, b) for a, b in itertools.product(P, P) if not PROSPECT.is_allowed(a, b)]
ILLEGAL_CAMPAIGN = [
    (a, b)
    for a, b in itertools.product(CampaignStatus, CampaignStatus)
    if not CAMPAIGN.is_allowed(a, b)
]


@pytest.mark.parametrize(("src", "dst"), ILLEGAL_PROSPECT)
async def test_every_illegal_prospect_pair_is_rejected_and_changes_nothing(src, dst):
    sc = Scenario()
    p = await sc.prospect(await sc.campaign(), status=src)
    audit_before = len(sc.store.audit)
    with pytest.raises(InvalidTransition):
        await sc.transitions.transition(p, dst, **KW)
    fresh = await sc.refresh(p)
    assert (fresh.status, fresh.version) == (src, p.version)
    assert len(sc.store.audit) == audit_before


@pytest.mark.parametrize(("src", "dst"), ILLEGAL_CAMPAIGN)
async def test_every_illegal_campaign_pair_is_rejected(src, dst):
    sc = Scenario()
    c = await sc.campaign(src)
    with pytest.raises(InvalidTransition):
        await sc.transitions.transition(c, dst, **KW)
    assert sc.store.campaigns[c.id].status is src


@pytest.mark.parametrize("src", [s for s in P if s is not P.SUPPRESSED and s is not P.WON])
def test_matrix_covers_all_non_terminal_to_suppressed(src):
    assert PROSPECT.is_allowed(src, P.SUPPRESSED)


async def test_campaign_pause_and_activate_bump_version_and_audit():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.DRAFT)
    r = await sc.transitions.transition(c, CampaignStatus.ACTIVE, **KW)
    assert r.entity.version == c.version + 1
    r2 = await sc.transitions.transition(r.entity, CampaignStatus.PAUSED, **KW)
    rows = [a for a in sc.store.audit if a["action"] == "campaign.transition"]
    assert [x["new_state"]["status"] for x in rows] == ["ACTIVE", "PAUSED"]
    assert rows[0]["old_state"] == {"status": "DRAFT", "version": 1}
    assert r2.event.type == "campaign.transitioned" and r2.event.event_id == rows[1]["id"]
    assert rows[0]["actor_id"] == "operator" and rows[0]["correlation_id"] == "c"


async def test_stale_entity_is_an_optimistic_lock_error_before_any_guard_runs():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.DRAFT)
    await sc.transitions.transition(c, CampaignStatus.ACTIVE, **KW)
    with pytest.raises(OptimisticLockError):
        await sc.transitions.transition(c, CampaignStatus.ACTIVE, **KW)  # c.version is stale


async def test_wrong_enum_and_blank_reason_are_invalid_input():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.DRAFT)
    with pytest.raises(InvalidInput):
        await sc.transitions.transition(c, P.ELIGIBLE, **KW)
    with pytest.raises(InvalidInput):
        await sc.transitions.transition(
            c, CampaignStatus.ACTIVE, actor=OPERATOR, reason="  ", correlation_id="c"
        )


async def test_activate_guard_fails_without_an_effective_policy():
    sc = Scenario()
    sc.store.global_version_id = None
    c = await sc.campaign(CampaignStatus.DRAFT)
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(c, CampaignStatus.ACTIVE, **KW)
    assert e.value.code == "POLICY_UNAVAILABLE"


async def test_audit_failure_rolls_back_the_status_change():
    sc = Scenario()
    c = await sc.campaign(CampaignStatus.DRAFT)
    sc.store.fail_audit = True
    with pytest.raises(RuntimeError):
        await sc.transitions.transition(c, CampaignStatus.ACTIVE, **KW)
    sc.store.fail_audit = False
    assert sc.store.campaigns[c.id].status is CampaignStatus.DRAFT
    assert sc.store.campaigns[c.id].version == c.version


async def test_guard_failure_rolls_back_writes_made_earlier_in_the_same_uow():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    with pytest.raises(GuardFailed):
        async with sc.factory() as uow:
            await uow.domains.get_or_create("sideeffect.com")  # earlier write in the same uow
            await sc.transitions.transition_in(uow, p, P.ELIGIBLE, **KW)  # no decision -> guard
    assert all(d.registered_domain != "sideeffect.com" for d in sc.store.domains.values())


# ---- prospect guards ----------------------------------------------------------------


async def test_eligible_requires_a_recorded_eligible_decision():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.ELIGIBLE, **KW)
    assert e.value.code == "QUALIFICATION_DECISION_REQUIRED"


async def test_forged_eligible_decision_cannot_pass_a_prospect_with_missing_metric():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    async with sc.factory() as uow:  # a decision row claiming ELIGIBLE with no metrics behind it
        await uow.qualifications.record(
            prospect_id=p.id,
            policy_version_id=sc.policy_version_id,
            outcome="ELIGIBLE",
            reason_codes=[],
            metric_snapshot={},
            missing_metrics=[],
        )
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.ELIGIBLE, **KW)
    assert e.value.code == "ELIGIBILITY_NOT_REPRODUCIBLE"
    assert e.value.detail["missing_metrics"] == [METRIC_DA, "spam_score"]


async def test_full_happy_path_with_stage_guards():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    await sc.good_metrics(p)
    p = (await sc.qualification.qualify(p.id, actor=OPERATOR, correlation_id="c")).prospect
    assert p.status is P.ELIGIBLE
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.RANKED, **KW)
    assert e.value.code == "RANKING_REQUIRED"
    async with sc.factory() as uow:
        await uow.rankings.record(
            prospect_id=p.id,
            policy_version_id=sc.policy_version_id,
            score=0.5,
            components={},
            explanation="x",
        )
    p = (await sc.transitions.transition(p, P.RANKED, **KW)).entity
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.RESEARCHED, **KW)
    assert e.value.code == "DOSSIER_REQUIRED"
    async with sc.factory() as uow:
        await uow.dossiers.create(prospect_id=p.id, summary="s", topics=[], editorial_signals={})
    p = (await sc.transitions.transition(p, P.RESEARCHED, **KW)).entity
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.CONTACT_READY, **KW)
    assert e.value.code == "CONTACT_CHANNEL_REQUIRED"
    async with sc.factory() as uow:
        c = await uow.contacts.create(
            domain_id=p.domain_id, name=None, role=None, contact_type="generic", confidence=0.5
        )
        await uow.contacts.add_channel(
            contact_id=c.id, channel_type="email", value_normalized="a@example.com", confidence=0.5
        )
    p = (await sc.transitions.transition(p, P.CONTACT_READY, **KW)).entity
    assert p.status is P.CONTACT_READY


async def test_suppressed_domain_blocks_progress_to_contact_ready():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign(), status=P.RESEARCHED)
    async with sc.factory() as uow:
        c = await uow.contacts.create(
            domain_id=p.domain_id, name=None, role=None, contact_type="generic", confidence=0.5
        )
        await uow.contacts.add_channel(
            contact_id=c.id, channel_type="email", value_normalized="a@example.com", confidence=0.5
        )
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.DOMAIN,
            value="example.com",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.CONTACT_READY, **KW)
    assert e.value.code == "SUPPRESSED"


async def test_to_suppressed_needs_a_real_match_and_cancels_pending_jobs():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign(), status=P.RANKED)
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.SUPPRESSED, **KW)
    assert e.value.code == "NO_SUPPRESSION_MATCH"
    async with sc.factory() as uow:
        await uow.jobs.enqueue(
            job_type="x",
            payload={},
            run_at=sc.store.clock.now(),
            idempotency_key="k1",
            subject_type="prospect",
            subject_id=p.id,
        )
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.DOMAIN,
            value="example.com",
            reason=SuppressionReason.LEGAL,
            actor=OPERATOR,
            correlation_id="s",
        )
    r = await sc.transitions.transition(p, P.SUPPRESSED, **KW)
    assert r.entity.status is P.SUPPRESSED
    assert [j["status"].value for j in sc.store.jobs.values()] == ["CANCELLED"]


async def test_rejected_to_discovered_requires_a_new_policy_version():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    p = (await sc.qualification.qualify(p.id, actor=OPERATOR, correlation_id="c")).prospect
    # DEFERRED (no metrics). Force a REJECTED via on_missing=reject is another policy; instead
    # drive REJECTED by bad metrics.
    await sc.good_metrics(p, da=10)
    p = (await sc.qualification.qualify(p.id, actor=OPERATOR, correlation_id="c")).prospect
    assert p.status is P.REJECTED
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.DISCOVERED, **KW)
    assert e.value.code == "NEW_POLICY_VERSION_REQUIRED"
    sc.store.add_policy()  # a newer global version becomes effective
    r = await sc.transitions.transition(p, P.DISCOVERED, **KW)
    assert r.entity.status is P.DISCOVERED


async def test_lost_to_eligible_fails_closed_until_a_cooldown_is_defined():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign(), status=P.LOST)
    with pytest.raises(GuardFailed) as e:
        await sc.transitions.transition(p, P.ELIGIBLE, **KW)
    assert e.value.code == "RECONTACT_COOLDOWN_UNDEFINED"
