import json

import pytest
from kb_agents.agents.contact import RoleAnnotations
from kb_agents.agents.discovery import QueryPlan
from kb_agents.agents.qualification import QualificationJudgement
from kb_agents.agents.research import ResearchClaims
from kb_contracts.agents import DiscoveryResult, DossierDraft, QualificationAssessment
from kb_contracts.providers import (
    InvalidResponse,
    ModelProvider,
    ModelRequest,
    ProviderUnavailable,
    RateLimited,
)
from kb_providers.model import FakeFailure, FakeModelProvider
from pydantic import BaseModel, ValidationError

MODELS: list[type[BaseModel]] = [
    QueryPlan,
    QualificationJudgement,
    ResearchClaims,
    RoleAnnotations,
    DiscoveryResult,
    DossierDraft,
    QualificationAssessment,
]


def _req(model: type[BaseModel] | None, prompt: str = "p") -> ModelRequest:
    return ModelRequest(
        system="s", prompt=prompt, output_schema=model.model_json_schema() if model else None
    )


def test_conforms_to_protocol() -> None:
    p = FakeModelProvider()
    assert isinstance(p, ModelProvider)
    assert p.is_paid is False


@pytest.mark.parametrize("model", MODELS)
async def test_structured_output_is_schema_valid(model: type[BaseModel]) -> None:
    resp = await FakeModelProvider(seed=3).generate(_req(model))
    assert resp.structured is not None
    model.model_validate(resp.structured)
    assert json.loads(resp.text) == resp.structured


async def test_reproducible_and_seedable() -> None:
    a = await FakeModelProvider(seed=1).generate(_req(QualificationJudgement))
    b = await FakeModelProvider(seed=1).generate(_req(QualificationJudgement))
    c = await FakeModelProvider(seed=2).generate(_req(QualificationJudgement))
    d = await FakeModelProvider(seed=1).generate(_req(QualificationJudgement, "other"))
    assert a == b
    assert a.structured != c.structured
    assert a.structured != d.structured


async def test_plain_text_when_no_schema() -> None:
    resp = await FakeModelProvider().generate(_req(None))
    assert resp.structured is None and resp.text


async def test_invalid_json_mode() -> None:
    resp = await FakeModelProvider(failure=FakeFailure.INVALID_JSON).generate(_req(DossierDraft))
    assert resp.structured is None
    with pytest.raises(ValueError):
        json.loads(resp.text)


@pytest.mark.parametrize("model", [QualificationJudgement, ResearchClaims, DossierDraft])
async def test_schema_violation_mode_breaks_schema(model: type[BaseModel]) -> None:
    resp = await FakeModelProvider(failure=FakeFailure.SCHEMA_VIOLATION).generate(_req(model))
    assert resp.structured is not None
    with pytest.raises(ValidationError):
        model.model_validate(resp.structured)


@pytest.mark.parametrize(
    ("mode", "exc"),
    [
        (FakeFailure.UNAVAILABLE, ProviderUnavailable),
        (FakeFailure.TIMEOUT, ProviderUnavailable),
        (FakeFailure.RATE_LIMITED, RateLimited),
        (FakeFailure.INVALID_RESPONSE, InvalidResponse),
    ],
)
async def test_raising_modes(mode: FakeFailure, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        await FakeModelProvider(failure=mode).generate(_req(DossierDraft))


async def test_failure_queue_then_recovers_and_health() -> None:
    p = FakeModelProvider(failures=[FakeFailure.UNAVAILABLE])
    with pytest.raises(ProviderUnavailable):
        await p.generate(_req(DossierDraft))
    assert (await p.generate(_req(DossierDraft))).structured is not None
    assert (await FakeModelProvider(failure=FakeFailure.UNAVAILABLE).health()).status == "down"
    assert (await p.health()).status == "ok"


async def test_scripted_responses_take_priority() -> None:
    p = FakeModelProvider(scripted=[{"queries": ["x"]}, "not json"])
    first = await p.generate(_req(QueryPlan))
    assert first.structured == {"queries": ["x"]}
    second = await p.generate(_req(QueryPlan))
    assert second.structured is None and second.text == "not json"
