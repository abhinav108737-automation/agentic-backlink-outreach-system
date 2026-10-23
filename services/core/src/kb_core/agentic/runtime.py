"""Assembles the agent runtime: gateway + real capabilities + run persistence + agents.

The gateway's recorder is the real ``AgentRunRepository`` (via ``RunStoreRecorder``), so every
tool call — including a blocked or invalid one — lands in ``tool_calls`` and is attributable to
an ``agent_runs`` row. The policy gate is the deterministic PolicyEngine, which an agent cannot
override: the gateway consults it before dispatch and a denial never reaches the capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from kb_agents.agents.contact import ContactAgent
from kb_agents.agents.discovery import DiscoveryAgent
from kb_agents.agents.qualification import QualificationAgent
from kb_agents.agents.research import ResearchAgent
from kb_agents.model_bridge import ModelRuntime
from kb_agents.runs.lifecycle import AgentRuntime
from kb_agents.tools.audit import RunStoreRecorder, ToolCallAudit
from kb_agents.tools.gateway import PolicyDecision, ToolGateway
from kb_contracts.enums import AgentName
from kb_contracts.providers import ModelProvider

from kb_core.agentic.capabilities import CapabilityDeps, build_capabilities
from kb_core.db.protocols import UnitOfWorkFactory

# Tools that cause a persistent write. The gate is consulted for these; pure reads are not
# gated, so a paused campaign can still be inspected but not written to.
_GATED_TOOLS = frozenset(
    {
        "record_candidates",
        "record_assessment",
        "record_evidence",
        "record_dossier",
        "record_contact_candidates",
    }
)


class UowRunStore:
    """Adapts ``AgentRunRepository`` to the ``AgentRunStore`` seam by opening a short unit of
    work per call. Run bookkeeping is deliberately NOT inside the caller's transaction: a run
    record must survive the failure it is recording."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def start(self, **kwargs: Any) -> UUID:
        async with self._uow_factory() as uow:
            return await uow.agent_runs.start(**kwargs)

    async def finish(self, agent_run_id: UUID, **kwargs: Any) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_runs.finish(agent_run_id, **kwargs)

    async def record_tool_call(self, **kwargs: Any) -> UUID:
        async with self._uow_factory() as uow:
            return await uow.agent_runs.record_tool_call(**kwargs)


class CampaignPolicyGate:
    """Deterministic pre-dispatch gate. Blocks writes while the kill switch is on or the
    campaign is not ACTIVE. An agent cannot bypass or override this."""

    def __init__(self, uow_factory: UnitOfWorkFactory, policy: Any, campaign_id: UUID) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._campaign_id = campaign_id

    async def check(self, agent: AgentName, tool: str, args: Any) -> PolicyDecision:
        if tool not in _GATED_TOOLS:
            return PolicyDecision(allowed=True)
        from kb_contracts.policy import PolicyCheckRequest

        async with self._uow_factory() as uow:
            decision = await self._policy.check_operational(
                uow,
                PolicyCheckRequest(
                    action="agent_write",
                    campaign_id=self._campaign_id,
                    subject_type="campaign",
                    subject_id=self._campaign_id,
                    context={"tool": tool, "agent": agent.value},
                    correlation_id="agent-gate",
                ),
            )
        return PolicyDecision(
            allowed=decision.allowed,
            reason=",".join(c.value for c in decision.block_codes),
            policy_version=str(decision.policy_version_id) if decision.policy_version_id else None,
        )


@dataclass(frozen=True)
class AgentStack:
    gateway: ToolGateway
    runs: UowRunStore
    runtime: AgentRuntime
    discovery: DiscoveryAgent
    qualification: QualificationAgent
    research: ResearchAgent
    contact: ContactAgent
    tool_calls: list[ToolCallAudit]


def build_agent_stack(
    *,
    deps: CapabilityDeps,
    model: ModelProvider,
    campaign_id: UUID | None = None,
    policy: Any | None = None,
    model_name: str | None = None,
) -> AgentStack:
    runs = UowRunStore(deps.uow_factory)
    seen: list[ToolCallAudit] = []
    recorder = _TeeRecorder(RunStoreRecorder(runs), seen)
    gate = (
        CampaignPolicyGate(deps.uow_factory, policy, campaign_id)
        if policy is not None and campaign_id is not None
        else None
    )
    gateway = ToolGateway(
        capabilities=build_capabilities(deps), recorder=recorder, policy_gate=gate
    )
    runtime = AgentRuntime(
        gateway=gateway, runs=runs, models=ModelRuntime(model, model_name=model_name)
    )
    return AgentStack(
        gateway=gateway,
        runs=runs,
        runtime=runtime,
        discovery=DiscoveryAgent(runtime),
        qualification=QualificationAgent(runtime),
        research=ResearchAgent(runtime),
        contact=ContactAgent(runtime),
        tool_calls=seen,
    )


class _TeeRecorder:
    """Records to the real store and keeps an in-process copy so a caller (or a test) can
    assert on the call sequence without querying the database."""

    def __init__(self, inner: Any, sink: list[ToolCallAudit]) -> None:
        self._inner = inner
        self._sink = sink

    async def record(self, audit: ToolCallAudit) -> None:
        self._sink.append(audit)
        await self._inner.record(audit)


__all__ = ["AgentStack", "CampaignPolicyGate", "UowRunStore", "build_agent_stack"]
