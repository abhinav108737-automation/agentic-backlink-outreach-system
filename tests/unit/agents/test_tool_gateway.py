"""Scope-enforcement matrix and gateway behaviour (SEC-04)."""

from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

import pytest
from kb_agents.runs.protocol import AgentRunStore
from kb_agents.testing import FakeWorld
from kb_agents.tools import (
    MemoryToolCallRecorder,
    PolicyDecision,
    RunStoreRecorder,
    ToolGateway,
    redact,
)
from kb_agents.tools.specs import TOOL_SPECS
from kb_contracts.enums import AgentName
from kb_contracts.providers import ProviderUnavailable
from kb_contracts.tools import (
    FORBIDDEN_CAPABILITIES,
    TOOL_ALLOWLIST,
    ToolResult,
    TypedToolRequest,
)

VOCABULARY = sorted(set().union(*TOOL_ALLOWLIST.values()) | FORBIDDEN_CAPABILITIES)

# Minimal valid args per tool, used to prove the *allowlist* (not the schema) decides.
_U = str(uuid4())
VALID_ARGS: dict[str, dict[str, Any]] = {
    "read_campaign_state": {"campaign_id": _U},
    "read_policy": {},
    "read_memory": {"query": "q"},
    "read_provider_health": {},
    "delegate_task": {"campaign_id": _U, "agent": "research", "description": "d"},
    "request_send": {"draft_id": _U, "rationale": "r"},
    "request_followup": {"opportunity_id": _U, "reason": "r"},
    "request_verification": {"backlink_id": _U},
    "request_report": {"campaign_id": _U},
    "propose_experiment": {"campaign_id": _U, "hypothesis": "h", "parameter": "p"},
    "escalate_to_human": {"reason": "r"},
    "search": {"query": "q"},
    "read_domain_metrics": {"domain": "a.com", "metrics": ["domain_authority"]},
    "fetch_page": {"url": "https://a.com/"},
    "read_evidence": {"prospect_id": _U},
    "read_thread": {"thread_ref": "t"},
    "read_backlink": {"backlink_id": _U},
    "read_report_snapshot": {"campaign_id": _U},
}


class Counting:
    """A capability that records that it was reached."""

    def __init__(self) -> None:
        self.reached: list[str] = []

    def make(self, tool: str) -> Any:
        async def cap(ctx: Any, args: Any) -> dict[str, Any]:
            self.reached.append(tool)
            return {}

        return cap


def _gateway(**kw: Any) -> tuple[ToolGateway, Counting, MemoryToolCallRecorder]:
    counting = Counting()
    recorder = MemoryToolCallRecorder()
    caps = {t: counting.make(t) for t in TOOL_SPECS}
    return ToolGateway(capabilities=caps, recorder=recorder, **kw), counting, recorder


def test_every_allowlisted_tool_has_a_typed_spec() -> None:
    allowlisted = set().union(*TOOL_ALLOWLIST.values())
    assert set(TOOL_SPECS) == allowlisted
    assert not set(TOOL_SPECS) & FORBIDDEN_CAPABILITIES


@pytest.mark.parametrize("agent", list(AgentName))
async def test_matrix_gateway_permits_exactly_the_allowlist(agent: AgentName) -> None:
    gateway, counting, recorder = _gateway()
    permitted: set[str] = set()
    for tool in VOCABULARY:
        result = await gateway.invoke(agent, TypedToolRequest(tool=tool, args={}))
        if result.policy_blocked:
            assert result.ok is False
            assert result.error_code in {"tool_not_allowed", "forbidden_capability"}
        else:
            permitted.add(tool)  # reached schema validation / dispatch
    assert permitted == set(TOOL_ALLOWLIST[agent])
    # only tools whose args are all optional can pass schema validation with {}
    assert set(counting.reached) <= {"read_policy", "read_provider_health"}
    assert len(recorder.calls) == len(VOCABULARY)  # blocked calls are audited too


@pytest.mark.parametrize("agent", list(AgentName))
async def test_matrix_valid_calls_reach_capability_only_when_allowed(agent: AgentName) -> None:
    gateway, counting, _ = _gateway()
    for tool, args in VALID_ARGS.items():
        await gateway.invoke(agent, TypedToolRequest(tool=tool, args=args))
    expected = {t for t in VALID_ARGS if t in TOOL_ALLOWLIST[agent]}
    assert set(counting.reached) == expected


@pytest.mark.parametrize("agent", list(AgentName))
@pytest.mark.parametrize("tool", sorted(FORBIDDEN_CAPABILITIES))
async def test_forbidden_capabilities_refused_for_every_agent(agent: AgentName, tool: str) -> None:
    gateway, counting, recorder = _gateway()
    result = await gateway.invoke(agent, TypedToolRequest(tool=tool, args={"sql": "drop"}))
    assert result == ToolResult(
        tool=tool, ok=False, error_code="forbidden_capability", policy_blocked=True
    )
    assert counting.reached == []
    assert recorder.calls[0].status == "blocked"


async def test_forbidden_refused_even_if_someone_registers_a_capability() -> None:
    reached: list[str] = []

    async def evil(ctx: Any, args: Any) -> dict[str, Any]:
        reached.append("x")
        return {}

    gateway = ToolGateway(
        capabilities={t: evil for t in FORBIDDEN_CAPABILITIES}, recorder=MemoryToolCallRecorder()
    )
    for t in FORBIDDEN_CAPABILITIES:
        r = await gateway.invoke(AgentName.OPERATOR, TypedToolRequest(tool=t))
        assert r.policy_blocked
    assert reached == []


async def test_outreach_cannot_send_but_operator_can_request_send() -> None:
    gateway, counting, _ = _gateway()
    args = VALID_ARGS["request_send"]
    for tool in ("send_email_direct", "request_send"):
        r = await gateway.invoke(AgentName.OUTREACH, TypedToolRequest(tool=tool, args=args))
        assert not r.ok and r.policy_blocked
    assert counting.reached == []
    ok = await gateway.invoke(AgentName.OPERATOR, TypedToolRequest(tool="request_send", args=args))
    assert ok.ok and counting.reached == ["request_send"]
    direct = await gateway.invoke(
        AgentName.OPERATOR, TypedToolRequest(tool="send_email_direct", args=args)
    )
    assert direct.policy_blocked and direct.error_code == "forbidden_capability"


async def test_unknown_tool_is_refused() -> None:
    gateway, _, _ = _gateway()
    r = await gateway.invoke(AgentName.DISCOVERY, TypedToolRequest(tool="made_up"))
    assert r.policy_blocked and r.error_code == "tool_not_allowed"


async def test_invalid_args_never_reach_capability() -> None:
    gateway, counting, recorder = _gateway()
    bad: list[dict[str, Any]] = [
        {},
        {"query": ""},
        {"query": "ok", "limit": 999},
        {"query": "ok", "extra": 1},
        {"query": "ok", "limit": float("nan")},
    ]
    for args in bad:
        r = await gateway.invoke(AgentName.DISCOVERY, TypedToolRequest(tool="search", args=args))
        assert not r.ok and r.error_code == "invalid_args" and not r.policy_blocked
    assert counting.reached == []
    assert {c.status for c in recorder.calls} == {"invalid_args"}
    bad_url = await gateway.invoke(
        AgentName.RESEARCH, TypedToolRequest(tool="fetch_page", args={"url": "file:///etc/passwd"})
    )
    assert bad_url.error_code == "invalid_args" and counting.reached == []


async def test_audit_records_tool_redacted_args_summary_status_latency() -> None:
    world = FakeWorld()
    world.serp["q"] = []
    recorder = MemoryToolCallRecorder()
    gateway = ToolGateway(capabilities=world.capabilities(), recorder=recorder)
    run_id = uuid4()
    await gateway.invoke(
        AgentName.DISCOVERY,
        TypedToolRequest(tool="search", args={"query": "q"}),
        agent_run_id=run_id,
        correlation_id="corr-1",
    )
    call = recorder.calls[0]
    assert (call.tool, call.status, call.agent) == ("search", "ok", "discovery")
    assert call.agent_run_id == run_id and call.correlation_id == "corr-1"
    assert call.result_summary["ok"] is True and call.latency_ms >= 0
    assert call.args_redacted == {"query": "q"}


def test_redaction() -> None:
    out = redact(
        {
            "api_key": "sk-123",
            "nested": {"Authorization": "Bearer abcdefghijkl", "ok": "fine"},
            "text": "Bearer abcdefghijklmnop leaked",
            "long": "x" * 500,
        }
    )
    assert out["api_key"] == "[REDACTED]" and out["nested"]["Authorization"] == "[REDACTED]"
    assert "abcdefghijklmnop" not in out["text"]
    assert out["long"].startswith("[500 chars sha256:")


async def test_capability_errors_become_results_not_exceptions() -> None:
    async def boom(ctx: Any, args: Any) -> dict[str, Any]:
        raise ProviderUnavailable("down")

    async def crash(ctx: Any, args: Any) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    recorder = MemoryToolCallRecorder()
    gw = ToolGateway(capabilities={"search": boom, "fetch_page": crash}, recorder=recorder)
    r1 = await gw.invoke(AgentName.DISCOVERY, TypedToolRequest(tool="search", args={"query": "q"}))
    assert r1.error_code == "provider_unavailable"
    r2 = await gw.invoke(
        AgentName.RESEARCH, TypedToolRequest(tool="fetch_page", args={"url": "https://a.com"})
    )
    assert r2.error_code == "capability_error:RuntimeError" and "secret" not in str(r2)
    r3 = await gw.invoke(
        AgentName.DISCOVERY, TypedToolRequest(tool="read_campaign_state", args={"campaign_id": _U})
    )
    assert r3.error_code == "capability_not_wired"


async def test_capability_output_is_validated() -> None:
    async def wrong(ctx: Any, args: Any) -> dict[str, Any]:
        return {"provider": "x", "results": [{"nope": 1}]}

    async def nan(ctx: Any, args: Any) -> dict[str, Any]:
        return {"domain": "a.com", "metrics": {"domain_authority": float("nan")}}

    gw = ToolGateway(
        capabilities={"search": wrong, "read_domain_metrics": nan},
        recorder=MemoryToolCallRecorder(),
    )
    r = await gw.invoke(AgentName.DISCOVERY, TypedToolRequest(tool="search", args={"query": "q"}))
    assert r.error_code == "invalid_tool_output" and r.data == {}
    n = await gw.invoke(
        AgentName.QUALIFICATION,
        TypedToolRequest(tool="read_domain_metrics", args=VALID_ARGS["read_domain_metrics"]),
    )
    assert n.error_code == "invalid_tool_output"


async def test_call_budget_is_bounded_per_run() -> None:
    gateway, counting, _ = _gateway(max_calls_per_run=2)
    run = uuid4()
    codes = [
        (
            await gateway.invoke(
                AgentName.DISCOVERY,
                TypedToolRequest(tool="search", args={"query": f"q{i}"}),
                agent_run_id=run,
            )
        ).error_code
        for i in range(4)
    ]
    assert "call_budget_exceeded" not in codes[:2]
    assert codes[2:] == ["call_budget_exceeded", "call_budget_exceeded"]
    assert len(counting.reached) == 2


async def test_mutating_calls_are_idempotent_within_a_run() -> None:
    gateway, counting, _ = _gateway()
    run = uuid4()
    req = TypedToolRequest(tool="request_send", args=VALID_ARGS["request_send"])
    await gateway.invoke(AgentName.OPERATOR, req, agent_run_id=run)
    await gateway.invoke(AgentName.OPERATOR, req, agent_run_id=run)
    assert counting.reached == ["request_send"]


async def test_policy_gate_can_deny_after_allowlist() -> None:
    class Deny:
        async def check(self, agent: AgentName, tool: str, args: Any) -> PolicyDecision:
            return PolicyDecision(False, "kill switch", "p1")

    gateway, counting, recorder = _gateway(policy_gate=Deny())
    r = await gateway.invoke(
        AgentName.OPERATOR, TypedToolRequest(tool="request_send", args=VALID_ARGS["request_send"])
    )
    assert r.policy_blocked and r.error_code == "policy_denied" and counting.reached == []
    assert recorder.calls[0].policy_decision == {
        "allowed": False,
        "reason": "kill switch",
        "policy_version": "p1",
    }


async def test_audit_failure_fails_closed() -> None:
    class Broken:
        async def record(self, audit: Any) -> None:
            raise RuntimeError("db down")

    world = FakeWorld()
    gw = ToolGateway(capabilities=world.capabilities(), recorder=Broken())
    r = await gw.invoke(AgentName.DISCOVERY, TypedToolRequest(tool="search", args={"query": "q"}))
    assert not r.ok and r.error_code == "audit_failed"


async def test_run_store_recorder_adapter_forwards_to_store() -> None:
    from kb_agents.runs import InMemoryAgentRunStore

    store = InMemoryAgentRunStore()
    world = FakeWorld()
    gw = ToolGateway(capabilities=world.capabilities(), recorder=RunStoreRecorder(store))
    run = uuid4()
    await gw.invoke(
        AgentName.DISCOVERY, TypedToolRequest(tool="search", args={"query": "q"}), agent_run_id=run
    )
    assert store.tool_calls[0]["tool_name"] == "search"
    assert store.tool_calls[0]["agent_run_id"] == run


def test_gateway_exposes_no_raw_handles() -> None:
    gw, _, _ = _gateway()
    toolbox = gw.bind(AgentName.DISCOVERY, uuid4())
    public = {n for n in dir(toolbox) if not n.startswith("_")}
    assert public == {"agent", "agent_run_id", "calls", "call"}
    sig = inspect.signature(AgentRunStore.start)
    assert "session" not in sig.parameters
