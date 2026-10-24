"""Negotiation: persist a plan per round and force human approval where required.

The agent proposes a `NegotiationPlan`; nothing it says commits the company. This service decides,
deterministically, whether the plan needs a human:

* `makes_binding_commitment` is true, OR
* `risk_level` is `high`, OR
* the draft response itself reads like a commitment (money, "we agree", guarantees, contract
  language). An agent cannot understate a commitment by leaving the flag false.

A plan that needs a human always creates a `negotiation_commitment` approval request. Routing:

  high risk                 -> ESCALATED_TO_HUMAN (a person takes over the conversation)
  binding, not high risk    -> AWAITING_APPROVAL
  otherwise                 -> stays NEGOTIATING (the draft may be used as a reply)

`AGREED` is reachable only through `resolve_approval`, which requires an APPROVED approval row.
No code path in this package moves an opportunity to AGREED on a plan's say-so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from kb_contracts.agents import NegotiationPlan
from kb_contracts.domain import ApprovalRequest, GuestPostOpportunity
from kb_contracts.enums import ApprovalKind, ApprovalStatus, OpportunityStatus
from kb_contracts.events import Actor

from kb_core.approvals.service import ApprovalService
from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.errors import DomainConflict, EntityNotFound
from kb_core.outreach.ports import ACTOR_NEGOTIATION, TransitionPort

_COMMITMENT = re.compile(
    r"[$€£]\s?\d|\b\d[\d,.]*\s?(usd|eur|gbp|dollars?|euros?)\b"
    r"|\bwe (agree|accept|confirm|commit|will pay|will publish|guarantee)\b"
    r"|\bi (agree|accept|confirm|commit)\b|\bdeal\b|\bguarantee[sd]?\b|\bbinding\b"
    r"|\bpay(ing|ment|ments)?\b|\bcontract\b|\bexclusive(ly)?\b|\binvoice\b",
    re.I,
)
_NEGOTIABLE = (OpportunityStatus.OPEN, OpportunityStatus.NEGOTIATING)
_PENDING_HUMAN = (OpportunityStatus.AWAITING_APPROVAL, OpportunityStatus.ESCALATED_TO_HUMAN)


def detect_commitment(text: str) -> bool:
    return bool(_COMMITMENT.search(text))


def needs_human(plan: NegotiationPlan) -> bool:
    return (
        plan.makes_binding_commitment
        or plan.risk_level == "high"
        or detect_commitment(plan.draft_response)
    )


@dataclass(frozen=True)
class RoundResult:
    opportunity: GuestPostOpportunity
    round_record: dict[str, Any]
    approval: ApprovalRequest | None
    requires_approval: bool
    route: str  # negotiating | awaiting_approval | escalated_to_human


class NegotiationService:
    def __init__(self, uow_factory: UnitOfWorkFactory, transitions: TransitionPort) -> None:
        self._uow_factory = uow_factory
        self._tr = transitions

    async def plan_round(
        self,
        plan: NegotiationPlan,
        *,
        correlation_id: str,
        idempotency_key: str | None = None,
        agent_run_id: UUID | None = None,
        actor: Actor = ACTOR_NEGOTIATION,
    ) -> RoundResult | None:
        """Persist one round. Returns None when `idempotency_key` was already applied."""
        async with self._uow_factory() as uow:
            if idempotency_key is not None:
                digest = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
                if not await uow.idempotency.claim(
                    scope="negotiation.plan", key=idempotency_key, request_hash=digest
                ):
                    return None
            opp = await uow.opportunities.get(plan.opportunity_id)
            if opp is None:
                raise EntityNotFound("opportunity", plan.opportunity_id)
            if opp.status not in _NEGOTIABLE:
                raise DomainConflict(
                    "OPPORTUNITY_NOT_NEGOTIABLE", f"opportunity is {opp.status.value}"
                )
            human = needs_human(plan)
            if opp.status is OpportunityStatus.OPEN:
                opp = (
                    await self._tr.transition_in(
                        uow,
                        opp,
                        OpportunityStatus.NEGOTIATING,
                        actor=actor,
                        reason="first negotiation round",
                        correlation_id=correlation_id,
                    )
                ).entity
            approval: ApprovalRequest | None = None
            if human:
                approval = await ApprovalService(self._uow_factory).create_in(
                    uow,
                    kind=ApprovalKind.NEGOTIATION_COMMITMENT,
                    subject_type="opportunity",
                    subject_id=opp.id,
                    requested_by=actor,
                    reason=(
                        f"risk={plan.risk_level} binding={plan.makes_binding_commitment} "
                        f"detected_commitment={detect_commitment(plan.draft_response)}"
                    ),
                    correlation_id=correlation_id,
                )
            record = await uow.opportunities.record_negotiation(
                opportunity_id=opp.id,
                draft=plan.draft_response,
                recommendation=plan.recommendation,
                risk_level=plan.risk_level,
                approval_request_id=approval.id if approval else None,
                agent_run_id=agent_run_id,
            )
            opp = await uow.opportunities.set_terms(
                opp.id, agreed_terms=dict(opp.agreed_terms), requires_approval=human
            )
            route = "negotiating"
            if human:
                target = (
                    OpportunityStatus.ESCALATED_TO_HUMAN
                    if plan.risk_level == "high"
                    else OpportunityStatus.AWAITING_APPROVAL
                )
                route = target.value.lower()
                opp = (
                    await self._tr.transition_in(
                        uow,
                        opp,
                        target,
                        actor=actor,
                        reason="commitment or high risk requires a human decision",
                        correlation_id=correlation_id,
                    )
                ).entity
            return RoundResult(opp, record, approval, human, route)

    async def resolve_approval(
        self, opportunity_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_NEGOTIATION
    ) -> GuestPostOpportunity:
        """Apply the operator's decision: APPROVED -> AGREED; REJECTED/EXPIRED -> NEGOTIATING."""
        async with self._uow_factory() as uow:
            opp = await uow.opportunities.get(opportunity_id)
            if opp is None:
                raise EntityNotFound("opportunity", opportunity_id)
            if opp.status not in _PENDING_HUMAN:
                raise DomainConflict(
                    "OPPORTUNITY_NOT_PENDING", f"opportunity is {opp.status.value}"
                )
            approval = await uow.approvals.latest_for_subject(
                subject_type="opportunity",
                subject_id=opp.id,
                kind=ApprovalKind.NEGOTIATION_COMMITMENT.value,
            )
            if approval is None or approval.status is ApprovalStatus.PENDING:
                raise DomainConflict("APPROVAL_PENDING", "no human decision has been recorded")
            if approval.status is ApprovalStatus.APPROVED:
                terms = {**opp.agreed_terms, "approval_request_id": str(approval.id)}
                await uow.opportunities.set_terms(
                    opp.id, agreed_terms=terms, requires_approval=opp.requires_approval
                )
                opp = (await uow.opportunities.get(opp.id)) or opp
                target = OpportunityStatus.AGREED
            else:
                target = OpportunityStatus.NEGOTIATING
            moved = await self._tr.transition_in(
                uow,
                opp,
                target,
                actor=actor,
                reason=f"approval {approval.id} {approval.status.value}",
                correlation_id=correlation_id,
            )
            result: GuestPostOpportunity = moved.entity
            return result
