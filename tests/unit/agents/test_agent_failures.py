"""Invalid model output is a recorded failure, never a silently coerced result."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from agent_support import make_rig, page
from kb_agents.agents import (
    ContactAgent,
    ContactTask,
    DiscoveryAgent,
    DiscoveryTask,
    QualificationAgent,
    QualificationTask,
    ResearchAgent,
    ResearchTask,
)
from kb_agents.agents.qualification import EvidenceExcerptIn
from kb_agents.model_bridge import ModelRuntime
from kb_agents.runs import AgentOutcome
from kb_agents.tools import PolicyDecision
from kb_contracts.providers import ProviderUnavailable
from kb_providers.model import FakeFailure, FakeModelProvider
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

URL = "https://example-health.org/"
NO_ROLE_HTML = '<html><body><p><a href="mailto:jo@example-health.org">jo</a></p></body></html>'


def _runners(rig: Any) -> dict[str, Any]:
    from uuid import uuid4

    async def discovery() -> AgentOutcome[Any]:
        rig.world.campaign["seed_queries"] = []  # force the model step
        return await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))

    async def qualification() -> AgentOutcome[Any]:
        rig.world.metrics["example-health.org"] = {"domain_authority": 50.0, "spam_score": 0.0}
        return await QualificationAgent(rig.runtime).run(
            QualificationTask(
                campaign_id=rig.campaign_id,
                prospect_id=rig.prospect_id,
                domain="example-health.org",
                evidence=(EvidenceExcerptIn(evidence_id=uuid4(), excerpt="x"),),
            )
        )

    async def research() -> AgentOutcome[Any]:
        return await ResearchAgent(rig.runtime).run(
            ResearchTask(
                campaign_id=rig.campaign_id,
                prospect_id=rig.prospect_id,
                domain="example-health.org",
                urls=(URL,),
            )
        )

    async def contact() -> AgentOutcome[Any]:
        rig.world.pages[URL] = page(URL, NO_ROLE_HTML)
        return await ContactAgent(rig.runtime).run(
            ContactTask(campaign_id=rig.campaign_id, prospect_id=rig.prospect_id, urls=(URL,))
        )

    return {
        "discovery": discovery,
        "qualification": qualification,
        "research": research,
        "contact": contact,
    }


AGENTS = ["discovery", "qualification", "research", "contact"]


def _assert_failed_and_recorded(rig: Any, out: AgentOutcome[Any], prefix: str) -> None:
    assert not out.ok and out.output is None
    assert out.record.failure is not None and out.record.failure.startswith(prefix), (
        out.record.failure
    )
    assert out.record.structured_result is None and out.record.confidence is None
    stored = rig.store.runs[out.record.agent_run_id]
    assert stored["finished"] and stored["failure"] == out.record.failure
    assert stored["structured_result"] is None
    assert not [
        c for c, _ in rig.world.recorded if c.startswith("record_") and c != "record_evidence"
    ]


@pytest.mark.parametrize("name", AGENTS)
async def test_malformed_json_is_schema_invalid_failure(name: str) -> None:
    model = FakeModelProvider(failure=FakeFailure.INVALID_JSON)
    rig = make_rig(model)
    out = await _runners(rig)[name]()
    _assert_failed_and_recorded(rig, out, "schema_invalid")
    assert len(model.requests) == 2  # one bounded corrective retry, then fail
    assert "sha256=" in out.record.failure  # type: ignore[operator]


@pytest.mark.parametrize("name", AGENTS)
async def test_schema_violating_output_is_failure(name: str) -> None:
    model = FakeModelProvider(failure=FakeFailure.SCHEMA_VIOLATION)
    rig = make_rig(model)
    out = await _runners(rig)[name]()
    _assert_failed_and_recorded(rig, out, "schema_invalid")


async def test_one_corrective_retry_can_recover() -> None:
    model = FakeModelProvider(
        failures=[FakeFailure.SCHEMA_VIOLATION], scripted=[{"queries": ["q one"]}]
    )
    rig = make_rig(model)
    rig.world.campaign["seed_queries"] = []
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    assert out.ok and len(model.requests) == 2
    assert "rejected" in model.requests[1].prompt  # the retry carries the validation feedback


@pytest.mark.parametrize("name", AGENTS)
@pytest.mark.parametrize(
    ("mode", "prefix"),
    [
        (FakeFailure.UNAVAILABLE, "provider_unavailable"),
        (FakeFailure.TIMEOUT, "provider_unavailable: timeout"),
        (FakeFailure.RATE_LIMITED, "rate_limited"),
        (FakeFailure.INVALID_RESPONSE, "provider_error:InvalidResponse"),
    ],
)
async def test_provider_failures_are_recorded(name: str, mode: FakeFailure, prefix: str) -> None:
    rig = make_rig(FakeModelProvider(failure=mode))
    out = await _runners(rig)[name]()
    _assert_failed_and_recorded(rig, out, prefix)


async def test_hung_provider_hits_run_timeout() -> None:
    rig = make_rig(FakeModelProvider(failure=FakeFailure.HANG))
    rig = dataclasses.replace(
        rig,
        runtime=dataclasses.replace(
            rig.runtime,
            models=ModelRuntime(FakeModelProvider(failure=FakeFailure.HANG), timeout_s=0.05),
        ),
    )
    out = await _runners(rig)["discovery"]()
    _assert_failed_and_recorded(rig, out, "provider_timeout")


async def test_fallback_provider_is_used_and_recorded() -> None:
    down = FakeModelProvider(failure=FakeFailure.UNAVAILABLE)
    backup = FakeModelProvider(scripted=[{"queries": ["q one"]}])
    backup.name = "backup-model"
    rig = make_rig(down)
    rig = dataclasses.replace(
        rig,
        runtime=dataclasses.replace(rig.runtime, models=ModelRuntime(down, fallbacks=[backup])),
    )
    out = await _runners(rig)["discovery"]()
    assert out.ok and out.record.fallback_used is True and out.record.provider == "backup-model"
    assert rig.store.runs[out.record.agent_run_id]["fallback_used"] is True


async def test_ungrounded_evidence_citation_fails_not_trimmed() -> None:
    from uuid import uuid4

    model = FakeModelProvider(
        scripted=[
            {
                "relevance": 0.5,
                "editorial_fit": 0.5,
                "evidence_ids": [str(uuid4())],
                "confidence": 0.5,
            }
        ]
    )
    rig = make_rig(model)
    out = await _runners(rig)["qualification"]()
    _assert_failed_and_recorded(rig, out, "ungrounded_evidence")


async def test_out_of_range_score_is_rejected_not_clamped() -> None:
    model = FakeModelProvider(
        scripted=[{"relevance": 1.7, "editorial_fit": 0.5, "confidence": 0.5}] * 2
    )
    rig = make_rig(model)
    out = await _runners(rig)["qualification"]()
    _assert_failed_and_recorded(rig, out, "schema_invalid")


async def test_extra_fields_in_model_output_rejected() -> None:
    bad = {"relevance": 0.5, "editorial_fit": 0.5, "confidence": 0.5, "eligible": True}
    rig = make_rig(FakeModelProvider(scripted=[bad, bad]))
    out = await _runners(rig)["qualification"]()
    _assert_failed_and_recorded(rig, out, "schema_invalid")


async def test_tool_failure_is_recorded_with_code() -> None:
    rig = make_rig(FakeModelProvider())
    rig.world.fail_tools["search"] = ProviderUnavailable("search down")
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    _assert_failed_and_recorded(rig, out, "tool_failed:search")
    assert "provider_unavailable" in (out.record.failure or "")


async def test_policy_gate_denial_surfaces_as_run_failure() -> None:
    class DenyRecords:
        async def check(self, agent: Any, tool: str, args: Any) -> PolicyDecision:
            return PolicyDecision(tool != "record_candidates", "frozen")

    rig = make_rig(FakeModelProvider(), policy_gate=DenyRecords())
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    assert not out.ok and "policy_denied" in (out.record.failure or "")
    assert rig.world.recorded == []


async def test_store_start_failure_means_no_tool_calls() -> None:
    rig = make_rig(FakeModelProvider())
    rig.store.fail_on_start = True
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    assert out.record.failure == "run_start_failed:RuntimeError" and out.output is None
    assert rig.recorder.calls == [] and out.record.tool_calls == ()


async def test_store_finish_failure_discards_unrecorded_result() -> None:
    rig = make_rig(FakeModelProvider())
    rig.store.fail_on_finish = True
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    assert out.output is None and "run_finish_failed" in (out.record.failure or "")


async def test_model_cannot_call_tools_even_when_it_tries() -> None:
    """A model that emits a tool call for a forbidden capability fails closed; the gateway is
    never asked to execute it because agents register no function tools."""

    async def hijack(messages: Any, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="send_email_direct", args={"to": "a@b.c"})]
        )

    rig = make_rig(FakeModelProvider(), pydantic_model=FunctionModel(hijack))
    rig.world.campaign["seed_queries"] = []
    out = await DiscoveryAgent(rig.runtime).run(DiscoveryTask(campaign_id=rig.campaign_id))
    assert not out.ok
    assert all(c.tool != "send_email_direct" for c in rig.recorder.calls)
    assert [c.tool for c in rig.recorder.calls] == ["read_campaign_state"]
    assert rig.world.recorded == []
