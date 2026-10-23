"""The I1 vertical slice: discovery → qualification → research → contact, agent-driven.

Agents produce typed proposals and persist only through ToolGateway capabilities. Every state
transition is made by the deterministic services, never by an agent: qualification's
ELIGIBLE/DEFERRED/REJECTED outcome comes from the PolicyEngine, and the agent's own assessment
is advisory and audited as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from kb_agents.agents.contact import ContactTask
from kb_agents.agents.qualification import QualificationTask
from kb_agents.agents.research import ResearchTask
from kb_contracts.enums import ProspectStatus, QualificationOutcome
from kb_contracts.events import Actor

from kb_core.agentic.runtime import AgentStack
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import GuardFailed
from kb_core.qualification.service import QualificationService
from kb_core.ranking.service import RankingService
from kb_core.transitions.service import TransitionService


@dataclass
class SliceOutcome:
    prospect_id: UUID
    domain: str
    outcome: QualificationOutcome | None = None
    missing_metrics: tuple[str, ...] = ()
    status: ProspectStatus | None = None
    rank_score: float | None = None
    researched: bool = False
    contacts: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def deferred(self) -> bool:
        return self.outcome is QualificationOutcome.DEFERRED


async def _urls_for(uow: UnitOfWork, prospect_id: UUID) -> tuple[str, ...]:
    rows = await uow.prospect_urls.list_for_prospect(prospect_id)
    return tuple(r.canonical_url for r in rows)


async def run_prospect_slice(
    *,
    stack: AgentStack,
    uow_factory: UnitOfWorkFactory,
    qualification: QualificationService,
    ranking: RankingService,
    transitions: TransitionService,
    campaign_id: UUID,
    prospect_id: UUID,
    actor: Actor,
    correlation_id: str,
    campaign_topics: tuple[str, ...] = (),
) -> SliceOutcome:
    """Carry one discovered prospect as far as its metrics allow.

    A DEFERRED prospect stops here deliberately: with a required metric missing it must not be
    researched or contacted (ADR-0015). That early return is the behaviour, not a shortcut.
    """
    async with uow_factory() as uow:
        prospect = await uow.prospects.get(prospect_id)
        if prospect is None:
            raise GuardFailed("PROSPECT_MISSING", f"no prospect {prospect_id}")
        domain = await uow.domains.get(prospect.domain_id)
        urls = await _urls_for(uow, prospect_id)
    host = domain.registered_domain if domain else ""
    out = SliceOutcome(prospect_id=prospect_id, domain=host)

    # 1. Qualification agent — advisory only. Its read_domain_metrics call is what persists the
    #    metric readings that the deterministic decision below then reads back.
    judged = await stack.qualification.run(
        QualificationTask(
            campaign_id=campaign_id,
            prospect_id=prospect_id,
            domain=host,
            campaign_topics=campaign_topics,
            correlation_id=correlation_id,
        )
    )
    if not judged.ok:
        out.notes.append(f"qualification agent failed: {judged.record.failure}")

    # 2. Deterministic decision. The agent cannot influence the outcome.
    result = await qualification.qualify(prospect_id, actor=actor, correlation_id=correlation_id)
    out.outcome = result.decision.outcome if result.decision else None
    out.missing_metrics = tuple(result.decision.missing_metrics) if result.decision else ()
    out.status = result.prospect.status

    if out.outcome is not QualificationOutcome.ELIGIBLE:
        out.notes.append(f"stopped after qualification: {out.outcome}")
        return out

    # 3. Ranking. ELIGIBLE -> RANKED is guarded by Lane B on a stored ranking result, so the
    #    ranking must be computed rather than the transition forced. The agent's advisory
    #    scores feed the signals; the weights and the score are deterministic.
    signals: dict[str, float] = {}
    if judged.ok and judged.output is not None:
        signals = {
            "relevance": judged.output.relevance,
            "editorial_fit": judged.output.editorial_fit,
        }
    ranked = await ranking.rank(prospect_id, signals, actor=actor, correlation_id=correlation_id)
    out.rank_score = ranked.ranking.score

    # 4. Research agent.
    if urls:
        researched = await stack.research.run(
            ResearchTask(
                campaign_id=campaign_id,
                prospect_id=prospect_id,
                domain=host,
                urls=urls,
                correlation_id=correlation_id,
            )
        )
        out.researched = researched.ok
        if not researched.ok:
            out.notes.append(f"research agent failed: {researched.record.failure}")

    # 5. Contact agent.
    if out.researched:
        contacted = await stack.contact.run(
            ContactTask(
                campaign_id=campaign_id,
                prospect_id=prospect_id,
                urls=urls,
                correlation_id=correlation_id,
            )
        )
        if contacted.ok and contacted.output is not None:
            out.contacts = len(contacted.output.candidates)
        else:
            out.notes.append(f"contact agent failed: {contacted.record.failure}")

    async with uow_factory() as uow:
        final = await uow.prospects.get(prospect_id)
        out.status = final.status if final else out.status
    return out


async def persist_discovery(
    *,
    stack: AgentStack,
    campaign_id: UUID,
    queries: tuple[str, ...] = (),
) -> Any:
    """Run the discovery agent; `record_candidates` persists through the gateway."""
    from kb_agents.agents.discovery import DiscoveryTask

    return await stack.discovery.run(
        DiscoveryTask(campaign_id=campaign_id, queries=queries, correlation_id="i1-discovery")
    )


__all__ = ["SliceOutcome", "persist_discovery", "run_prospect_slice"]
