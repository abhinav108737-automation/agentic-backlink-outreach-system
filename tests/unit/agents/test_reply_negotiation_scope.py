"""Tool-scope matrix for the Reply and Negotiation agents (SEC-04, C4 allowlist)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from agent_support import make_rig
from kb_agents.agents import NegotiationAgent, ReplyAgent, ReplyTask
from kb_agents.testing import FakeWorld
from kb_agents.tools import MemoryToolCallRecorder, ToolGateway
from kb_agents.tools.specs import TOOL_SPECS
from kb_contracts.enums import AgentName, ReplyClass
from kb_contracts.tools import (
    FORBIDDEN_CAPABILITIES,
    TOOL_ALLOWLIST,
    ToolResult,
    TypedToolRequest,
)
from kb_providers.model import FakeModelProvider
from reply_support import (
    THREAD,
    add_thread,
    deterministic,
    echo_reply,
    negotiation_draft,
    negotiation_task,
    scenario,
)

REPLY_TOOLS = {"read_thread", "read_memory", "record_classification"}
NEGOTIATION_TOOLS = {"read_thread", "read_policy", "read_memory", "record_negotiation_plan"}
_U = str(uuid4())
_OPP = str(uuid4())

VALID_ARGS: dict[str, dict[str, Any]] = {
    "read_thread": {"thread_ref": "t"},
    "read_memory": {"query": "q"},
    "read_policy": {},
    "record_classification": {
        "thread_ref": "t",
        "result": {"reply_class": "positive", "confidence": 0.5, "next_action": "no_action"},
    },
    "record_negotiation_plan": {
        "plan": {
            "opportunity_id": _OPP,
            "draft_response": "hello",
            "recommendation": "reply_with_draft",
            "risk_level": "low",
        }
    },
}
EVERYTHING = sorted(set(TOOL_SPECS) | FORBIDDEN_CAPABILITIES)
AGENTS = [(AgentName.REPLY, REPLY_TOOLS), (AgentName.NEGOTIATION, NEGOTIATION_TOOLS)]


class Counting:
    def __init__(self) -> None:
        self.reached: list[str] = []

    def make(self, tool: str) -> Any:
        async def cap(ctx: Any, args: Any) -> dict[str, Any]:
            self.reached.append(tool)
            return {}

        return cap


def _gateway() -> tuple[ToolGateway, Counting, MemoryToolCallRecorder]:
    counting = Counting()
    recorder = MemoryToolCallRecorder()
    caps = {t: counting.make(t) for t in TOOL_SPECS}
    return ToolGateway(capabilities=caps, recorder=recorder), counting, recorder


@pytest.mark.parametrize(("agent", "tools"), AGENTS, ids=lambda v: str(getattr(v, "value", "")))
def test_allowlist_is_exactly_the_documented_set(agent: AgentName, tools: set[str]) -> None:
    assert TOOL_ALLOWLIST[agent] == tools
    assert not tools & FORBIDDEN_CAPABILITIES


@pytest.mark.parametrize(("agent", "tools"), AGENTS, ids=lambda v: str(getattr(v, "value", "")))
async def test_each_agent_reaches_exactly_its_allowlist_and_nothing_else(
    agent: AgentName, tools: set[str]
) -> None:
    gateway, counting, recorder = _gateway()
    refused: set[str] = set()
    for tool in EVERYTHING:
        args = VALID_ARGS.get(tool, {})
        result = await gateway.invoke(agent, TypedToolRequest(tool=tool, args=args))
        if result.policy_blocked:
            refused.add(tool)
    # `read_policy` has all-optional args; the others are exercised with valid args above
    assert set(counting.reached) == tools
    assert refused == set(EVERYTHING) - tools
    assert len(recorder.calls) == len(EVERYTHING)  # refusals are audited too


@pytest.mark.parametrize("agent", [AgentName.REPLY, AgentName.NEGOTIATION])
@pytest.mark.parametrize("tool", sorted(FORBIDDEN_CAPABILITIES))
async def test_all_seven_forbidden_capabilities_are_refused(agent: AgentName, tool: str) -> None:
    assert len(FORBIDDEN_CAPABILITIES) == 7
    gateway, counting, recorder = _gateway()
    result = await gateway.invoke(agent, TypedToolRequest(tool=tool, args={"x": "y"}))
    assert result == ToolResult(
        tool=tool, ok=False, error_code="forbidden_capability", policy_blocked=True
    )
    assert counting.reached == [] and recorder.calls[0].status == "blocked"


async def test_record_negotiation_plan_is_refused_for_reply() -> None:
    gateway, counting, recorder = _gateway()
    result = await gateway.invoke(
        AgentName.REPLY,
        TypedToolRequest(
            tool="record_negotiation_plan", args=VALID_ARGS["record_negotiation_plan"]
        ),
    )
    assert result.error_code == "tool_not_allowed" and result.policy_blocked
    assert counting.reached == [] and recorder.calls[0].status == "blocked"


async def test_record_classification_is_refused_for_negotiation() -> None:
    gateway, counting, recorder = _gateway()
    result = await gateway.invoke(
        AgentName.NEGOTIATION,
        TypedToolRequest(tool="record_classification", args=VALID_ARGS["record_classification"]),
    )
    assert result.error_code == "tool_not_allowed" and result.policy_blocked
    assert counting.reached == [] and recorder.calls[0].status == "blocked"


@pytest.mark.parametrize(
    "tool", ["request_send", "request_followup", "escalate_to_human", "record_draft", "search"]
)
@pytest.mark.parametrize("agent", [AgentName.REPLY, AgentName.NEGOTIATION])
async def test_no_send_followup_escalation_or_draft_tool_for_either_agent(
    agent: AgentName, tool: str
) -> None:
    gateway, counting, _ = _gateway()
    result = await gateway.invoke(agent, TypedToolRequest(tool=tool, args={}))
    assert result.policy_blocked and counting.reached == []


async def test_real_runs_only_touch_allowlisted_tools_with_ok_status() -> None:
    reply = scenario("positive-interested")["reply"]
    rig = make_rig(FakeModelProvider(scripted=[echo_reply(ReplyClass.POSITIVE)]))
    add_thread(rig, subject=reply["subject"], body=reply["body"])
    r = await ReplyAgent(rig.runtime).run(
        ReplyTask(
            campaign_id=rig.campaign_id,
            thread_ref=THREAD,
            deterministic=deterministic(reply["from_address"], reply["subject"], reply["body"]),
        )
    )
    assert r.ok
    neg_rig = make_rig(FakeModelProvider(scripted=[negotiation_draft()]))
    n = await NegotiationAgent(neg_rig.runtime).run(negotiation_task(neg_rig))
    assert n.ok
    for rg, agent, tools in (
        (rig, AgentName.REPLY, REPLY_TOOLS),
        (neg_rig, AgentName.NEGOTIATION, NEGOTIATION_TOOLS),
    ):
        assert {c.tool for c in rg.recorder.calls} <= tools
        assert {c.agent for c in rg.recorder.calls} == {agent.value}
        assert all(c.status == "ok" for c in rg.recorder.calls)


def test_fake_world_wires_the_three_capabilities_these_agents_call() -> None:
    caps = FakeWorld().capabilities()
    assert {"read_thread", "record_classification", "record_negotiation_plan"} <= set(caps)
