"""Guards for structurally-legal edges (docs/13 enforcement rule 2). Deterministic only.

A guard either returns or raises `GuardFailed`. Guards read through the same unit of work the
transition writes in, so what they validated is what gets committed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from kb_contracts.domain import OutreachMessage, Prospect
from kb_contracts.enums import (
    ApprovalKind,
    ApprovalStatus,
    CampaignStatus,
    MessageStatus,
    ProspectStatus,
    QualificationOutcome,
)
from kb_contracts.events import Actor

from kb_core.db.protocols import UnitOfWork
from kb_core.errors import EntityNotFound, GuardFailed, PolicyUnavailable
from kb_core.policy.claims import unsupported_claims
from kb_core.qualification.evaluator import evaluate, metrics_to_fetch
from kb_core.suppression.service import check_suppression


@dataclass(frozen=True)
class GuardContext:
    uow: UnitOfWork
    entity: Any  # the freshly reloaded row (Prospect | Campaign), never the caller's stale copy
    src: StrEnum
    dst: StrEnum
    actor: Actor
    reason: str
    correlation_id: str
    now: datetime


Guard = Callable[[GuardContext], Awaitable[None]]
# (machine, src, dst); src None matches any source ("any non-terminal -> SUPPRESSED").
GuardKey = tuple[str, str | None, str]
GuardRegistry = Mapping[GuardKey, tuple[Guard, ...]]

P = ProspectStatus
_OUTCOME_FOR_TARGET = {
    P.ELIGIBLE: QualificationOutcome.ELIGIBLE,
    P.REJECTED: QualificationOutcome.REJECTED,
    P.DEFERRED: QualificationOutcome.DEFERRED,
}


def _prospect(ctx: GuardContext) -> Prospect:
    assert isinstance(ctx.entity, Prospect)
    return ctx.entity


async def qualification_decision_matches(ctx: GuardContext) -> None:
    """DISCOVERED/DEFERRED -> ELIGIBLE/REJECTED/DEFERRED only on a recorded decision with that
    outcome. For ELIGIBLE the metrics are re-evaluated against the live readings under the
    decision's own policy version, so a stale or forged ELIGIBLE row cannot pass a prospect whose
    required metric is missing (ADR-0015)."""
    prospect = _prospect(ctx)
    decision = await ctx.uow.qualifications.latest(prospect.id)
    wanted = _OUTCOME_FOR_TARGET[P(ctx.dst.value)]
    if decision is None or decision.outcome is not wanted:
        raise GuardFailed(
            "QUALIFICATION_DECISION_REQUIRED",
            f"no {wanted.value} qualification decision recorded for this prospect",
        )
    if wanted is not QualificationOutcome.ELIGIBLE:
        return
    version = await ctx.uow.policies.get_version(decision.policy_version_id)
    if version is None:
        raise GuardFailed("POLICY_VERSION_MISSING", "decision references an unknown policy")
    latest = await ctx.uow.domain_metrics.latest(prospect.domain_id, metrics_to_fetch(version.body))
    check = evaluate(version.body, latest)
    if check.outcome is not QualificationOutcome.ELIGIBLE:
        raise GuardFailed(
            "ELIGIBILITY_NOT_REPRODUCIBLE",
            "live metrics no longer satisfy the policy: " + ", ".join(check.reason_codes),
            {"missing_metrics": list(check.missing_metrics)},
        )


async def ranking_stored(ctx: GuardContext) -> None:
    if await ctx.uow.rankings.latest(_prospect(ctx).id) is None:
        raise GuardFailed("RANKING_REQUIRED", "a ranking result must be stored first")


async def dossier_stored(ctx: GuardContext) -> None:
    if await ctx.uow.dossiers.latest(_prospect(ctx).id) is None:
        raise GuardFailed("DOSSIER_REQUIRED", "a research dossier must be stored first")


async def contact_channel_exists(ctx: GuardContext) -> None:
    if not await ctx.uow.contacts.list_channels(_prospect(ctx).domain_id):
        raise GuardFailed("CONTACT_CHANNEL_REQUIRED", "no contact channel found for the domain")


async def new_policy_version_required(ctx: GuardContext) -> None:
    """REJECTED -> DISCOVERED only via re-qualification under a *new* policy version."""
    prospect = _prospect(ctx)
    decision = await ctx.uow.qualifications.latest(prospect.id)
    current = await ctx.uow.policies.active_version_for_campaign(prospect.campaign_id)
    if current is None:
        raise PolicyUnavailable(f"no effective policy for campaign {prospect.campaign_id}")
    if decision is not None and decision.policy_version_id == current.id:
        raise GuardFailed(
            "NEW_POLICY_VERSION_REQUIRED",
            "re-qualification requires a policy version newer than the one that rejected it",
        )


async def recontact_cooldown_undefined(ctx: GuardContext) -> None:
    """LOST -> ELIGIBLE needs a policy-defined cooldown. PolicyBody has none yet (TBD), so the
    edge fails closed rather than inventing a number."""
    raise GuardFailed(
        "RECONTACT_COOLDOWN_UNDEFINED", "re-contact cooldown is not defined by policy (TBD)"
    )


async def not_suppressed(ctx: GuardContext) -> None:
    prospect = _prospect(ctx)
    domain = await ctx.uow.domains.get(prospect.domain_id)
    if domain is None:
        raise EntityNotFound("domain", prospect.domain_id)
    result = await check_suppression(
        ctx.uow, campaign_id=prospect.campaign_id, domain=domain.registered_domain
    )
    if result.suppressed:
        raise GuardFailed("SUPPRESSED", "the prospect's domain is suppressed")


async def suppression_matches(ctx: GuardContext) -> None:
    """any non-terminal -> SUPPRESSED requires an actual matching suppression entry."""
    prospect = _prospect(ctx)
    domain = await ctx.uow.domains.get(prospect.domain_id)
    if domain is None:
        raise EntityNotFound("domain", prospect.domain_id)
    result = await check_suppression(
        ctx.uow, campaign_id=prospect.campaign_id, domain=domain.registered_domain
    )
    if not result.suppressed:
        raise GuardFailed("NO_SUPPRESSION_MATCH", "no active suppression entry matches")


async def campaign_policy_resolvable(ctx: GuardContext) -> None:
    if await ctx.uow.policies.active_version_for_campaign(ctx.entity.id) is None:
        raise GuardFailed("POLICY_UNAVAILABLE", "campaign has no effective policy version")


def _message(ctx: GuardContext) -> OutreachMessage:
    assert isinstance(ctx.entity, OutreachMessage)
    return ctx.entity


async def _message_approval_status(ctx: GuardContext) -> ApprovalStatus | None:
    latest = await ctx.uow.approvals.latest_for_subject(
        subject_type="message", subject_id=_message(ctx).id, kind=ApprovalKind.MESSAGE_SEND.value
    )
    return latest.status if latest else None


async def claims_supported(ctx: GuardContext) -> None:
    """DRAFT -> QA_PASSED: every factual claim maps to persisted evidence (docs/13 2b)."""
    message = _message(ctx)
    seq = await ctx.uow.sequences.get(message.sequence_id)
    if seq is None:
        raise EntityNotFound("outreach_sequence", message.sequence_id)
    bad = await unsupported_claims(ctx.uow, message, seq.prospect_id)
    if bad:
        raise GuardFailed(
            "UNSUPPORTED_CLAIM",
            f"{len(bad)} claim(s) have no persisted evidence",
            {"claim_indexes": list(bad)},
        )


async def approval_granted(ctx: GuardContext) -> None:
    """PENDING_APPROVAL -> APPROVED needs an APPROVED message_send approval."""
    if await _message_approval_status(ctx) is not ApprovalStatus.APPROVED:
        raise GuardFailed("APPROVAL_REQUIRED", "no APPROVED message_send approval on record")


async def approval_rejected(ctx: GuardContext) -> None:
    """PENDING_APPROVAL -> REJECTED needs a REJECTED message_send approval."""
    if await _message_approval_status(ctx) is not ApprovalStatus.REJECTED:
        raise GuardFailed("APPROVAL_NOT_REJECTED", "no REJECTED message_send approval on record")


async def approval_not_required(ctx: GuardContext) -> None:
    """QA_PASSED -> APPROVED skips the queue only when the policy does not require approval
    for sends, or the approval is already granted."""
    message = _message(ctx)
    seq = await ctx.uow.sequences.get(message.sequence_id)
    if seq is None:
        raise EntityNotFound("outreach_sequence", message.sequence_id)
    version = await ctx.uow.policies.active_version_for_campaign(seq.campaign_id)
    if version is None:
        raise PolicyUnavailable(f"no effective policy for campaign {seq.campaign_id}")
    if version.body.require_approval_for_send and (
        await _message_approval_status(ctx) is not ApprovalStatus.APPROVED
    ):
        raise GuardFailed("APPROVAL_REQUIRED", "policy requires an operator approval for sends")


def default_guards() -> dict[GuardKey, tuple[Guard, ...]]:
    g: dict[GuardKey, tuple[Guard, ...]] = {}

    def add(machine: str, src: str | None, dst: str, *guards: Guard) -> None:
        g[(machine, src, dst)] = (*g.get((machine, src, dst), ()), *guards)

    for src in (P.DISCOVERED, P.DEFERRED):
        for dst in (P.ELIGIBLE, P.REJECTED, P.DEFERRED):
            add("prospect", src.value, dst.value, qualification_decision_matches)
    add("prospect", P.ELIGIBLE.value, P.RANKED.value, ranking_stored)
    add("prospect", P.RANKED.value, P.RESEARCHED.value, dossier_stored)
    add(
        "prospect",
        P.RESEARCHED.value,
        P.CONTACT_READY.value,
        contact_channel_exists,
        not_suppressed,
    )
    add("prospect", P.CONTACT_READY.value, P.OUTREACH_READY.value, not_suppressed)
    add("prospect", P.OUTREACH_READY.value, P.IN_OUTREACH.value, not_suppressed)
    add("prospect", P.REJECTED.value, P.DISCOVERED.value, new_policy_version_required)
    add("prospect", P.LOST.value, P.ELIGIBLE.value, recontact_cooldown_undefined)
    add("prospect", None, P.SUPPRESSED.value, suppression_matches)
    M = MessageStatus
    add("outreach_message", M.DRAFT.value, M.QA_PASSED.value, claims_supported)
    add("outreach_message", M.PENDING_APPROVAL.value, M.APPROVED.value, approval_granted)
    add("outreach_message", M.PENDING_APPROVAL.value, M.REJECTED.value, approval_rejected)
    add("outreach_message", M.QA_PASSED.value, M.APPROVED.value, approval_not_required)
    for src_c in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
        add("campaign", src_c.value, CampaignStatus.ACTIVE.value, campaign_policy_resolvable)
    return g
