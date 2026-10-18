"""Qualification: evaluate hard eligibility, record the decision, move the prospect.

Everything happens in one unit of work: the decision row, the status change and its audit row
commit together or not at all. Agents never call this to set a status; they may request it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from kb_contracts.domain import Prospect, QualificationDecision
from kb_contracts.enums import ProspectStatus, QualificationOutcome
from kb_contracts.events import Actor, DomainEvent
from kb_contracts.policy import BlockCode, PolicyCheckRequest

from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, GuardFailed, PolicyBlocked, PolicyUnavailable
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.qualification.evaluator import Evaluation, evaluate, explain, metrics_to_fetch
from kb_core.transitions.service import TransitionService

_OUTCOME_STATE = {
    QualificationOutcome.ELIGIBLE: ProspectStatus.ELIGIBLE,
    QualificationOutcome.REJECTED: ProspectStatus.REJECTED,
    QualificationOutcome.DEFERRED: ProspectStatus.DEFERRED,
}
_QUALIFIABLE = (ProspectStatus.DISCOVERED, ProspectStatus.DEFERRED)


@dataclass(frozen=True)
class QualificationResult:
    prospect: Prospect
    decision: QualificationDecision | None  # None when the prospect was suppressed instead
    evaluation: Evaluation | None
    explanation: str
    events: tuple[DomainEvent, ...]


class QualificationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        policy: DeterministicPolicyEngine,
        transitions: TransitionService,
    ) -> None:
        self._uow_factory = uow_factory
        self._policy = policy
        self._transitions = transitions

    async def qualify(
        self, prospect_id: UUID, *, actor: Actor, correlation_id: str
    ) -> QualificationResult:
        async with self._uow_factory() as uow:
            return await self.qualify_in(
                uow, prospect_id, actor=actor, correlation_id=correlation_id
            )

    async def qualify_in(
        self, uow: UnitOfWork, prospect_id: UUID, *, actor: Actor, correlation_id: str
    ) -> QualificationResult:
        prospect = await uow.prospects.get(prospect_id)
        if prospect is None:
            raise EntityNotFound("prospect", prospect_id)
        if prospect.status not in _QUALIFIABLE:
            raise GuardFailed(
                "PROSPECT_NOT_QUALIFIABLE",
                f"a {prospect.status.value} prospect is not qualified again here",
            )

        gate = await self._policy.check_operational(
            uow,
            PolicyCheckRequest(
                action="qualify",
                campaign_id=prospect.campaign_id,
                subject_type="prospect",
                subject_id=prospect.id,
                correlation_id=correlation_id,
            ),
        )
        if BlockCode.SUPPRESSED in gate.block_codes:
            moved = await self._transitions.transition_in(
                uow,
                prospect,
                ProspectStatus.SUPPRESSED,
                actor=actor,
                reason="suppression entry matches the prospect's domain",
                correlation_id=correlation_id,
            )
            return QualificationResult(
                moved.entity, None, None, "Suppressed: a suppression entry matches.", (moved.event,)
            )
        if not gate.allowed:
            raise PolicyBlocked(gate)

        version = await uow.policies.active_version_for_campaign(prospect.campaign_id)
        if version is None:
            raise PolicyUnavailable(f"no effective policy for campaign {prospect.campaign_id}")
        # latest() is the only source of availability: key absent = never fetched, None = fetched
        # but unavailable. DomainMetric.value is never consulted for this decision.
        latest = await uow.domain_metrics.latest(prospect.domain_id, metrics_to_fetch(version.body))
        evaluation = evaluate(version.body, latest)
        decision = await uow.qualifications.record(
            prospect_id=prospect.id,
            policy_version_id=version.id,
            outcome=evaluation.outcome.value,
            reason_codes=evaluation.reason_codes,
            metric_snapshot=evaluation.metric_snapshot,
            missing_metrics=evaluation.missing_metrics,
        )

        target = _OUTCOME_STATE[evaluation.outcome]
        events: tuple[DomainEvent, ...] = ()
        if target is not prospect.status:  # DEFERRED -> DEFERRED re-check changes nothing
            moved = await self._transitions.transition_in(
                uow,
                prospect,
                target,
                actor=actor,
                reason=f"qualification: {', '.join(evaluation.reason_codes)}",
                correlation_id=correlation_id,
            )
            prospect, events = moved.entity, (moved.event,)
        return QualificationResult(prospect, decision, evaluation, explain(evaluation), events)
