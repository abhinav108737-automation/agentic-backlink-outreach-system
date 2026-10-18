"""Ranking service: compute, store, and (from ELIGIBLE) advance the prospect to RANKED."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from kb_contracts.domain import Prospect, RankingResult
from kb_contracts.enums import ProspectStatus
from kb_contracts.events import Actor, DomainEvent

from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, GuardFailed, PolicyUnavailable
from kb_core.qualification.evaluator import METRIC_DA, MetricState, classify_metric
from kb_core.ranking.compute import compute_ranking
from kb_core.transitions.service import TransitionService


@dataclass(frozen=True)
class RankingOutcome:
    prospect: Prospect
    ranking: RankingResult
    events: tuple[DomainEvent, ...]


class RankingService:
    def __init__(self, uow_factory: UnitOfWorkFactory, transitions: TransitionService) -> None:
        self._uow_factory = uow_factory
        self._transitions = transitions

    async def rank(
        self,
        prospect_id: UUID,
        signals: Mapping[str, float],
        *,
        actor: Actor,
        correlation_id: str,
    ) -> RankingOutcome:
        async with self._uow_factory() as uow:
            return await self.rank_in(
                uow, prospect_id, signals, actor=actor, correlation_id=correlation_id
            )

    async def rank_in(
        self,
        uow: UnitOfWork,
        prospect_id: UUID,
        signals: Mapping[str, float],
        *,
        actor: Actor,
        correlation_id: str,
    ) -> RankingOutcome:
        prospect = await uow.prospects.get(prospect_id)
        if prospect is None:
            raise EntityNotFound("prospect", prospect_id)
        if prospect.status not in (ProspectStatus.ELIGIBLE, ProspectStatus.RANKED):
            raise GuardFailed(
                "PROSPECT_NOT_ELIGIBLE",
                f"only ELIGIBLE or RANKED prospects are ranked, not {prospect.status.value}",
            )
        version = await uow.policies.active_version_for_campaign(prospect.campaign_id)
        if version is None:
            raise PolicyUnavailable(f"no effective policy for campaign {prospect.campaign_id}")

        merged = dict(signals)
        if "authority" not in merged:  # derive from a *known* DA only; unavailable stays absent
            latest = await uow.domain_metrics.latest(prospect.domain_id, [METRIC_DA])
            da = latest.get(METRIC_DA)
            if classify_metric(latest, METRIC_DA) is MetricState.AVAILABLE and da is not None:
                merged["authority"] = min(max(da / 100.0, 0.0), 1.0)

        computed = compute_ranking(version.body.ranking_weights, merged)
        ranking = await uow.rankings.record(
            prospect_id=prospect.id,
            policy_version_id=version.id,
            score=computed.score,
            components=computed.components,
            explanation=computed.explanation,
        )
        events: tuple[DomainEvent, ...] = ()
        if prospect.status is ProspectStatus.ELIGIBLE:
            moved = await self._transitions.transition_in(
                uow,
                prospect,
                ProspectStatus.RANKED,
                actor=actor,
                reason=f"ranked with score {computed.score:.3f}",
                correlation_id=correlation_id,
            )
            prospect, events = moved.entity, (moved.event,)
        return RankingOutcome(prospect, ranking, events)
