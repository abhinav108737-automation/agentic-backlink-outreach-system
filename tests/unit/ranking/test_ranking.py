import math

import pytest
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import ProspectStatus
from kb_core.audit.service import OPERATOR
from kb_core.errors import GuardFailed, InvalidInput
from kb_core.ranking.compute import compute_ranking
from kb_core.ranking.service import RankingService
from kb_core.testing.scenario import Scenario

W = {"authority": 0.6, "relevance": 0.4}


def test_weighted_score_components_and_explanation():
    r = compute_ranking(W, {"authority": 0.5, "relevance": 1.0})
    assert r.score == pytest.approx(0.7)
    assert r.components == {"authority": pytest.approx(0.3), "relevance": pytest.approx(0.4)}
    assert "authority 0.300" in r.explanation and "weight 0.60" in r.explanation


def test_unavailable_signal_is_excluded_and_named_never_zeroed():
    r = compute_ranking({"authority": 0.5, "traffic": 0.5}, {"authority": 0.8})
    assert r.score == pytest.approx(0.8)  # renormalised, not halved by a fabricated 0
    assert "not available" in r.explanation and "traffic" in r.explanation


def test_unweighted_signals_ignored_and_equal_weights_when_policy_has_none():
    r = compute_ranking(W, {"authority": 1.0, "relevance": 1.0, "extra": 0.0})
    assert "extra" in r.explanation and "ignored" in r.explanation and r.score == pytest.approx(1.0)
    eq = compute_ranking({}, {"a": 1.0, "b": 0.0})
    assert eq.score == pytest.approx(0.5) and "weighted equally" in eq.explanation


@pytest.mark.parametrize("sig", [{"authority": 1.5}, {"authority": -0.1}, {"authority": math.nan}])
def test_invalid_signals_rejected(sig):
    with pytest.raises(InvalidInput):
        compute_ranking(W, sig)


def test_invalid_weights_and_no_overlap_rejected():
    with pytest.raises(InvalidInput):
        compute_ranking({"a": -1.0}, {"a": 0.5})
    with pytest.raises(InvalidInput):
        compute_ranking(W, {"unrelated": 0.5})


async def test_service_records_ranking_and_moves_eligible_to_ranked_atomically():
    sc = Scenario(PolicyBody(ranking_weights=W))
    p = await sc.prospect(await sc.campaign())
    await sc.good_metrics(p, da=80)
    p = (await sc.qualification.qualify(p.id, actor=OPERATOR, correlation_id="c")).prospect
    svc = RankingService(sc.factory, sc.transitions)
    out = await svc.rank(p.id, {"relevance": 0.5}, actor=OPERATOR, correlation_id="c")
    assert out.prospect.status is ProspectStatus.RANKED
    assert out.ranking.components["authority"] == pytest.approx(0.6 * 0.8)  # derived from DA 80
    assert out.ranking.policy_version_id == sc.policy_version_id
    again = await svc.rank(p.id, {"relevance": 0.9}, actor=OPERATOR, correlation_id="c")
    assert again.events == () and again.prospect.status is ProspectStatus.RANKED


async def test_unavailable_da_is_not_turned_into_an_authority_signal():
    sc = Scenario(PolicyBody(ranking_weights=W, required_metrics=("spam_score",)))
    p = await sc.prospect(await sc.campaign(), status=ProspectStatus.ELIGIBLE)
    await sc.metric(p, "domain_authority", None)
    out = await RankingService(sc.factory, sc.transitions).rank(
        p.id, {"relevance": 0.5}, actor=OPERATOR, correlation_id="c"
    )
    assert "authority" not in out.ranking.components and "authority" in out.ranking.explanation


async def test_only_eligible_or_ranked_prospects_are_ranked():
    sc = Scenario()
    p = await sc.prospect(await sc.campaign())
    with pytest.raises(GuardFailed):
        await RankingService(sc.factory, sc.transitions).rank(
            p.id, {"a": 0.5}, actor=OPERATOR, correlation_id="c"
        )
