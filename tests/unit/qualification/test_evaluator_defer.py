"""ADR-0015: three metric states, and DEFER for states 2 and 3 asserted separately."""

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import ProspectStatus, QualificationOutcome
from kb_core.qualification.evaluator import METRIC_DA, METRIC_SPAM, evaluate
from kb_core.testing.scenario import Scenario

BODY = PolicyBody()


def test_state1_numeric_values_above_thresholds_are_eligible():
    ev = evaluate(BODY, {METRIC_DA: 60.0, METRIC_SPAM: 0.5})
    assert ev.outcome is QualificationOutcome.ELIGIBLE and ev.missing_metrics == ()


def test_state2_fetched_but_unavailable_is_deferred_not_passed_or_rejected():
    ev = evaluate(BODY, {METRIC_DA: None, METRIC_SPAM: 0.2})
    assert ev.outcome is QualificationOutcome.DEFERRED
    assert ev.missing_metrics == (METRIC_DA,)
    assert f"METRIC_UNAVAILABLE:{METRIC_DA}" in ev.reason_codes
    assert not any(c.startswith("METRIC_NEVER_FETCHED") for c in ev.reason_codes)
    assert ev.metric_snapshot[METRIC_DA] is None  # recorded as unavailable, not 0


def test_state3_never_fetched_is_deferred_not_passed_or_rejected():
    ev = evaluate(BODY, {METRIC_SPAM: 0.2})  # DA key absent
    assert ev.outcome is QualificationOutcome.DEFERRED
    assert ev.missing_metrics == (METRIC_DA,)
    assert f"METRIC_NEVER_FETCHED:{METRIC_DA}" in ev.reason_codes
    assert not any(c.startswith("METRIC_UNAVAILABLE") for c in ev.reason_codes)
    assert METRIC_DA not in ev.metric_snapshot  # absent key stays absent


def test_state2_and_state3_are_distinguishable_in_the_record():
    unavailable = evaluate(BODY, {METRIC_DA: None, METRIC_SPAM: 0.2})
    never = evaluate(BODY, {METRIC_SPAM: 0.2})
    assert unavailable.reason_codes != never.reason_codes
    assert unavailable.metric_snapshot != never.metric_snapshot


def test_everything_missing_is_deferred_with_both_listed():
    ev = evaluate(BODY, {})
    assert ev.outcome is QualificationOutcome.DEFERRED
    assert ev.missing_metrics == (METRIC_DA, METRIC_SPAM)


def test_on_missing_reject_rejects_only_when_policy_says_so():
    ev = evaluate(PolicyBody(on_missing_metric="reject"), {METRIC_SPAM: 0.2})
    assert ev.outcome is QualificationOutcome.REJECTED


def test_policy_can_never_pass_on_missing():
    with pytest.raises(ValueError):
        PolicyBody(on_missing_metric="pass")


@pytest.mark.parametrize(
    ("da", "spam", "failed"),
    [(45.0, 0.0, "DA_NOT_ABOVE_MIN"), (46.0, 1.0, "SPAM_NOT_BELOW_MAX"), (10.0, 5.0, None)],
)
def test_strict_thresholds_reject(da, spam, failed):
    ev = evaluate(BODY, {METRIC_DA: da, METRIC_SPAM: spam})
    assert ev.outcome is QualificationOutcome.REJECTED
    if failed:
        assert failed in ev.reason_codes


def test_missing_dominates_a_known_failure_so_it_defers():
    ev = evaluate(BODY, {METRIC_DA: 10.0})  # spam never fetched
    assert ev.outcome is QualificationOutcome.DEFERRED
    assert "DA_NOT_ABOVE_MIN" in ev.reason_codes


def test_non_finite_reading_is_unavailable_not_a_value():
    ev = evaluate(BODY, {METRIC_DA: float("nan"), METRIC_SPAM: 0.1})
    assert ev.outcome is QualificationOutcome.DEFERRED


async def test_service_state2_defers_prospect_and_records_truthfully():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    await sc.metric(p, METRIC_DA, None)
    await sc.metric(p, METRIC_SPAM, 0.1)
    res = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert res.prospect.status is ProspectStatus.DEFERRED
    assert res.decision.outcome is QualificationOutcome.DEFERRED
    assert res.decision.missing_metrics == (METRIC_DA,)
    assert res.decision.metric_snapshot[METRIC_DA] is None


async def test_service_state3_defers_prospect_and_records_truthfully():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    await sc.metric(p, METRIC_SPAM, 0.1)
    res = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert res.prospect.status is ProspectStatus.DEFERRED
    assert METRIC_DA not in res.decision.metric_snapshot
    assert res.decision.missing_metrics == (METRIC_DA,)
    assert "never fetched" in res.explanation


async def test_deferred_prospect_becomes_eligible_when_metrics_arrive():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    first = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert first.prospect.status is ProspectStatus.DEFERRED
    again = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert again.events == () and again.prospect.status is ProspectStatus.DEFERRED
    await sc.good_metrics(p)
    ok = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert ok.prospect.status is ProspectStatus.ELIGIBLE


async def test_newest_unavailable_reading_is_not_masked_by_older_value():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    await sc.good_metrics(p)
    await sc.metric(p, METRIC_DA, None)  # a later refetch found nothing
    res = await sc.qualification.qualify(p.id, actor=_A, correlation_id="c")
    assert res.prospect.status is ProspectStatus.DEFERRED


from kb_core.audit.service import SYSTEM_QUALIFICATION as _A  # noqa: E402
