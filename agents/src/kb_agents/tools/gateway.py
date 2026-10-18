"""ToolGateway: the single choke point through which agents act (SEC-04, ADR-0005).

Import boundary: ``kb_agents`` may not import ``kb_core.db`` or the email/crawler/browser
providers. Every side-effecting capability is therefore an *injected* async callable
``(ToolContext, validated_args) -> dict`` registered by tool name. The Lead wires real
repositories/crawler behind those callables at I1; agents and this module never see a session,
connection, raw provider or secret. Nothing here raises into the agent: a refusal, a bad
argument, a capability error or an audit failure all come back as ``ToolResult(ok=False)``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from kb_contracts.enums import AgentName
from kb_contracts.providers import ProviderError, ProviderUnavailable, RateLimited
from kb_contracts.tools import (
    FORBIDDEN_CAPABILITIES,
    TOOL_ALLOWLIST,
    ToolResult,
    TypedToolRequest,
    tool_allowed,
)
from pydantic import ValidationError

from kb_agents.tools.audit import ToolCallAudit, ToolCallRecorder, redact, summarise_data
from kb_agents.tools.specs import TOOL_SPECS, ToolModel

log = logging.getLogger(__name__)

DEFAULT_MAX_CALLS_PER_RUN = 40


@dataclass(frozen=True)
class ToolContext:
    """Attribution handed to a capability. Carries no credentials or handles."""

    agent: AgentName
    agent_run_id: UUID | None
    idempotency_key: str | None
    correlation_id: str | None


Capability = Callable[[ToolContext, Any], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    policy_version: str | None = None


class ToolPolicyGate(Protocol):
    """Optional deterministic gate (e.g. the PolicyEngine) consulted before dispatch."""

    async def check(self, agent: AgentName, tool: str, args: ToolModel) -> PolicyDecision: ...


def derive_idempotency_key(
    agent: AgentName, run_id: UUID | None, tool: str, args: dict[str, Any]
) -> str:
    material = json.dumps([agent.value, str(run_id), tool, args], sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()


class ToolGateway:
    def __init__(
        self,
        *,
        capabilities: Mapping[str, Capability],
        recorder: ToolCallRecorder,
        policy_gate: ToolPolicyGate | None = None,
        max_calls_per_run: int = DEFAULT_MAX_CALLS_PER_RUN,
    ) -> None:
        self._capabilities = dict(capabilities)
        self._recorder = recorder
        self._gate = policy_gate
        self._max_calls = max_calls_per_run
        self._counts: dict[UUID | None, int] = {}
        self._replay: dict[tuple[UUID | None, str], ToolResult] = {}

    def bind(
        self, agent: AgentName, agent_run_id: UUID | None, *, correlation_id: str | None = None
    ) -> Toolbox:
        return Toolbox(self, agent, agent_run_id, correlation_id)

    async def invoke(
        self,
        agent: AgentName,
        request: TypedToolRequest,
        *,
        agent_run_id: UUID | None = None,
        correlation_id: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        tool = request.tool
        args_redacted = redact(request.args)

        async def finish(
            result: ToolResult, status: str, decision: dict[str, Any] | None = None
        ) -> ToolResult:
            audit = ToolCallAudit(
                agent=agent.value,
                agent_run_id=agent_run_id,
                tool=tool,
                args_redacted=args_redacted,
                result_summary={
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "policy_blocked": result.policy_blocked,
                    "data": summarise_data(result.data),
                },
                status=status,
                policy_decision=decision,
                latency_ms=int((time.perf_counter() - started) * 1000),
                correlation_id=correlation_id,
                idempotency_key=request.idempotency_key,
            )
            try:
                await self._recorder.record(audit)
            except Exception:  # fail closed: an unaudited call is not a success
                log.exception("tool-call audit failed for %s", tool)
                return ToolResult(tool=tool, ok=False, error_code="audit_failed")
            return result

        # 1. scope: allowlist + forbidden capabilities (deterministic, before anything else)
        if tool in FORBIDDEN_CAPABILITIES:
            return await finish(
                ToolResult(
                    tool=tool, ok=False, error_code="forbidden_capability", policy_blocked=True
                ),
                "blocked",
                {"rule": "forbidden_capability"},
            )
        if not tool_allowed(agent, tool):
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="tool_not_allowed", policy_blocked=True),
                "blocked",
                {"rule": "tool_allowlist", "agent": agent.value},
            )
        spec = TOOL_SPECS.get(tool)
        if spec is None:  # allowlisted but no typed schema: refuse, never dispatch untyped
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="unknown_tool", policy_blocked=True),
                "blocked",
                {"rule": "no_tool_spec"},
            )

        # 2. bounded calls per run
        count = self._counts.get(agent_run_id, 0)
        if count >= self._max_calls:
            return await finish(
                ToolResult(
                    tool=tool, ok=False, error_code="call_budget_exceeded", policy_blocked=True
                ),
                "blocked",
                {"rule": "call_budget", "max": self._max_calls},
            )
        self._counts[agent_run_id] = count + 1

        # 3. typed argument validation: invalid args never reach a capability
        try:
            args = spec.args_model.model_validate(request.args)
        except ValidationError as exc:
            return await finish(
                ToolResult(
                    tool=tool,
                    ok=False,
                    error_code="invalid_args",
                    data={
                        "errors": [
                            f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()
                        ]
                    },
                ),
                "invalid_args",
            )

        # 4. deterministic policy gate
        decision: dict[str, Any] | None = None
        if self._gate is not None:
            verdict = await self._gate.check(agent, tool, args)
            decision = {
                "allowed": verdict.allowed,
                "reason": verdict.reason,
                "policy_version": verdict.policy_version,
            }
            if not verdict.allowed:
                return await finish(
                    ToolResult(
                        tool=tool, ok=False, error_code="policy_denied", policy_blocked=True
                    ),
                    "blocked",
                    decision,
                )

        # 5. idempotency for mutating tools (replay within a run returns the first result)
        key = request.idempotency_key
        if spec.mutating and key is None:
            key = derive_idempotency_key(agent, agent_run_id, tool, request.args)
        if spec.mutating and key is not None and (agent_run_id, key) in self._replay:
            return await finish(self._replay[(agent_run_id, key)], "ok", decision)

        capability = self._capabilities.get(tool)
        if capability is None:
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="capability_not_wired"),
                "error",
                decision,
            )

        # 6. dispatch
        ctx = ToolContext(agent, agent_run_id, key, correlation_id)
        try:
            raw = await capability(ctx, args)
        except ProviderUnavailable:
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="provider_unavailable"),
                "error",
                decision,
            )
        except RateLimited:
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="rate_limited"), "error", decision
            )
        except ProviderError as exc:
            return await finish(
                ToolResult(tool=tool, ok=False, error_code=f"provider_error:{type(exc).__name__}"),
                "error",
                decision,
            )
        except Exception as exc:
            log.exception("capability %s failed", tool)
            return await finish(
                ToolResult(
                    tool=tool, ok=False, error_code=f"capability_error:{type(exc).__name__}"
                ),
                "error",
                decision,
            )

        # 7. validate capability output; never hand an agent unvalidated data
        try:
            if spec.result_model is not None:
                data = spec.result_model.model_validate(dict(raw)).model_dump(mode="json")
            else:
                data = json.loads(json.dumps(dict(raw), allow_nan=False, default=str))
        except (ValidationError, ValueError, TypeError):
            return await finish(
                ToolResult(tool=tool, ok=False, error_code="invalid_tool_output"),
                "invalid_output",
                decision,
            )
        result = ToolResult(tool=tool, ok=True, data=data)
        if spec.mutating and key is not None:
            self._replay[(agent_run_id, key)] = result
        return await finish(result, "ok", decision)


class Toolbox:
    """A gateway handle bound to one agent and one run. This is all an agent ever holds."""

    def __init__(
        self,
        gateway: ToolGateway,
        agent: AgentName,
        agent_run_id: UUID | None,
        correlation_id: str | None,
    ) -> None:
        self._gateway = gateway
        self.agent = agent
        self.agent_run_id = agent_run_id
        self._correlation_id = correlation_id
        self.calls: list[str] = []

    async def call(self, tool: str, args: ToolModel | dict[str, Any]) -> ToolResult:
        payload = args.model_dump(mode="json") if isinstance(args, ToolModel) else dict(args)
        self.calls.append(tool)
        return await self._gateway.invoke(
            self.agent,
            TypedToolRequest(tool=tool, args=payload),
            agent_run_id=self.agent_run_id,
            correlation_id=self._correlation_id,
        )


def allowed_tools(agent: AgentName) -> frozenset[str]:
    return TOOL_ALLOWLIST[agent]
