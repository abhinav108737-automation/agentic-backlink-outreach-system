"""Assembles the agent runtime: gateway + real capabilities + run persistence + agents.

The gateway's recorder is the real ``AgentRunRepository`` (via ``RunStoreRecorder``), so every
tool call — including a blocked or invalid one — lands in ``tool_calls`` and is attributable to
an ``agent_runs`` row. The policy gate is the deterministic PolicyEngine, which an agent cannot
override: the gateway consults it before dispatch and a denial never reaches the capability.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
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
from kb_contracts.agents import NegotiationPlan
from kb_contracts.config import Settings
from kb_contracts.enums import AgentName
from kb_contracts.providers import Clock, CrawlerProvider, EmailProvider, ModelProvider
from kb_core.approvals.service import ApprovalService
from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.negotiation.service import NegotiationService, detect_commitment
from kb_core.outreach.context import TemplateDraftProducer
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.scheduling.worker import JobHandler
from kb_core.sending.service import SendService
from kb_core.transitions.service import TransitionService
from kb_providers.crawler import MockCrawlerProvider
from kb_providers.email import MockEmailProvider
from kb_providers.search import MockSearchProvider
from kb_providers.seo_metrics import MockSEOMetricsProvider

from kb_runtime.capabilities import CapabilityDeps, build_capabilities
from kb_runtime.jobs import WorkerDeps, build_registry

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


__all__ = [
    "AgentStack",
    "CampaignPolicyGate",
    "OutreachStack",
    "UowRunStore",
    "build_agent_stack",
    "build_outreach_stack",
]


# ============================================================ Wave 2B: the outreach stack
# The composition root for the worker side. This is the only place that sees the deterministic
# services, the providers, the agent runtime and the job registry at once.


@dataclass(frozen=True)
class OutreachStack:
    registry: Mapping[str, JobHandler]
    send_gate: SendService
    negotiation: NegotiationService
    transitions: TransitionService
    policy: DeterministicPolicyEngine
    email: EmailProvider
    crawler: CrawlerProvider
    agents: AgentStack | None = None


def _deterministic_plan_producer(
    detect: Callable[[str], bool],
) -> Callable[..., Awaitable[NegotiationPlan]]:
    """Offline fallback when no Negotiation agent is wired.

    It never proposes terms. It drafts a holding reply and defers to a human whenever the
    inbound text looks like a commitment, which keeps the deterministic guarantee that nothing
    commits without an approval.
    """

    async def produce(opportunity: Any, reply: Any, classification: Any = None) -> NegotiationPlan:
        binding = detect(reply.body_sanitized)
        return NegotiationPlan(
            opportunity_id=opportunity.id,
            draft_response=(
                "Thank you for the reply. We are reviewing the details and will come back to "
                "you shortly."
            ),
            recommendation="hold_for_human_review" if binding else "reply_with_draft",
            risk_level="medium" if binding else "low",
            makes_binding_commitment=binding,
        )

    return produce


def build_outreach_stack(
    *,
    settings: Settings,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    email: EmailProvider | None = None,
    crawler: CrawlerProvider | None = None,
    model: ModelProvider | None = None,
    fixture_root: Any = None,
    campaign_id: UUID | None = None,
    lost_after_misses: int = 3,
) -> OutreachStack:
    """Assemble everything the worker needs.

    `email` and `crawler` default to the fixture-backed mocks: **there is no code path here
    that can reach a real mail server**, and `REAL_SEND_ENABLED` is additionally enforced by
    the policy engine. Pass a `model` to get real Reply/Negotiation agents; without one the
    deterministic producers are used and nothing silently depends on a model being available.
    """
    email = email or MockEmailProvider(fixture_root)
    crawler = crawler or MockCrawlerProvider(fixture_root)

    policy = DeterministicPolicyEngine(uow_factory, settings)
    transitions = TransitionService(uow_factory, clock)
    approvals = ApprovalService(uow_factory)
    send_gate = SendService(uow_factory, policy, transitions, approvals, clock)
    negotiation = NegotiationService(uow_factory, transitions)

    agents: AgentStack | None = None
    plan_producer: Callable[..., Awaitable[NegotiationPlan]] = _deterministic_plan_producer(
        detect_commitment
    )
    if model is not None:
        agents = build_agent_stack(
            deps=CapabilityDeps(
                uow_factory=uow_factory,
                search=MockSearchProvider(fixture_root),
                crawler=crawler,
                seo_metrics=MockSEOMetricsProvider(fixture_root),
                transitions=transitions,
                negotiation=negotiation,
            ),
            model=model,
            campaign_id=campaign_id,
            policy=policy,
        )

    registry = build_registry(
        WorkerDeps(
            uow_factory=uow_factory,
            transitions=transitions,
            policy=policy,
            clock=clock,
            email=email,
            crawler=crawler,
            send_gate=send_gate,
            draft_producer=TemplateDraftProducer(),
            plan_producer=plan_producer,
            lost_after_misses=lost_after_misses,
        )
    )
    return OutreachStack(
        registry=registry,
        send_gate=send_gate,
        negotiation=negotiation,
        transitions=transitions,
        policy=policy,
        email=email,
        crawler=crawler,
        agents=agents,
    )
