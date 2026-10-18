"""Deterministic policy gate (C6). No LLM, no network, no provider: pure reads and rules.

`check` evaluates every applicable rule and returns *all* triggered block codes (not just the
first), so the operator sees the complete reason an action is refused. No code path accepts an
override: an agent, a prompt or an argument cannot turn a block into an allow (ADR-0005).

Block codes this engine emits, and their exact triggers:

  KILL_SWITCH_ACTIVE     system_settings.kill_switch is active (or malformed: fail closed);
                         applies to every action.
  CAMPAIGN_PAUSED        outbound action (enqueue_send/send/follow_up) and campaign is not
                         ACTIVE; any other action and campaign is PAUSED or ARCHIVED.
  SUPPRESSED             an active global or campaign-scoped suppression matches the email,
                         its domain, the domain, the contact, or the subject prospect's domain.
  METRIC_MISSING         action qualifies/sends for a prospect and a required metric is
                         unavailable (fetched, no value) or never fetched.
  HARD_THRESHOLD_FAILED  a known DA is not > min_da, or a known spam score is not < max_spam.
  REAL_SEND_DISABLED     send/enqueue_send with delivery_mode "real" (the default when unstated)
                         while REAL_SEND_ENABLED is false.
  APPROVAL_REQUIRED      send/enqueue_send without an APPROVED message_send approval while the
                         policy requires one; negotiation_commit without an APPROVED commitment
                         approval; or real sending enabled without an APPROVED
                         live_send_activation for the campaign.

Other BlockCodes in the frozen enum (UNSUPPORTED_CLAIM, DUPLICATE_*, SEND_LIMIT_EXCEEDED, ...)
belong to the outreach lane and are intentionally not emitted here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from kb_contracts.config import Settings
from kb_contracts.domain import Campaign, PolicyVersion
from kb_contracts.enums import ApprovalKind, ApprovalStatus, CampaignStatus
from kb_contracts.policy import BlockCode, PolicyCheckRequest, PolicyDecision

from kb_core.audit.service import SYSTEM_POLICY, AuditTrail
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, InvalidInput, PolicyUnavailable
from kb_core.qualification.evaluator import evaluate, metrics_to_fetch
from kb_core.settings.service import read_kill_switch
from kb_core.suppression.service import check_suppression

OUTBOUND_ACTIONS = frozenset({"enqueue_send", "send", "follow_up"})
SEND_ACTIONS = frozenset({"enqueue_send", "send"})
ELIGIBILITY_ACTIONS = frozenset({"qualify"}) | OUTBOUND_ACTIONS
COMMIT_ACTIONS = frozenset({"negotiation_commit"})


class DeterministicPolicyEngine:
    def __init__(self, uow_factory: UnitOfWorkFactory, settings: Settings) -> None:
        self._uow_factory = uow_factory
        self._settings = settings

    async def check(self, request: PolicyCheckRequest) -> PolicyDecision:
        async with self._uow_factory() as uow:
            return await self.check_in(uow, request)

    async def check_in(self, uow: UnitOfWork, request: PolicyCheckRequest) -> PolicyDecision:
        """Full gate: operational rules plus (where relevant) hard eligibility."""
        return await self._decide(uow, request, include_eligibility=True)

    async def check_operational(
        self, uow: UnitOfWork, request: PolicyCheckRequest
    ) -> PolicyDecision:
        """Operational rules only (kill switch, pause, suppression, send gates). Used by the
        qualification service, which computes eligibility itself and must not treat a DEFER
        as a block."""
        return await self._decide(uow, request, include_eligibility=False)

    # ------------------------------------------------------------------------------------

    async def _decide(
        self, uow: UnitOfWork, request: PolicyCheckRequest, *, include_eligibility: bool
    ) -> PolicyDecision:
        campaign = await uow.campaigns.get(request.campaign_id)
        if campaign is None:
            raise EntityNotFound("campaign", request.campaign_id)
        version = await uow.policies.active_version_for_campaign(request.campaign_id)
        if version is None:
            raise PolicyUnavailable(f"no effective policy for campaign {request.campaign_id}")

        codes: list[BlockCode] = []
        detail: dict[str, Any] = {"action": request.action}

        if (await read_kill_switch(uow)).active:
            codes.append(BlockCode.KILL_SWITCH_ACTIVE)

        if self._campaign_blocks(campaign, request.action):
            codes.append(BlockCode.CAMPAIGN_PAUSED)
            detail["campaign_status"] = campaign.status.value

        prospect_domain_id = await self._prospect_domain_id(uow, request)
        if await self._is_suppressed(uow, request, prospect_domain_id, detail):
            codes.append(BlockCode.SUPPRESSED)

        if request.action in SEND_ACTIONS:
            await self._send_gates(uow, request, version, codes, detail)
        elif request.action in COMMIT_ACTIONS:
            await self._commitment_gate(uow, request, version, codes)

        if include_eligibility and request.action in ELIGIBILITY_ACTIONS and prospect_domain_id:
            await self._eligibility(uow, version, prospect_domain_id, codes, detail)

        unique = tuple(dict.fromkeys(codes))
        decision = PolicyDecision(
            allowed=not unique,
            block_codes=unique,
            requires_approval=BlockCode.APPROVAL_REQUIRED in unique,
            policy_version_id=version.id,
            detail=detail,
        )
        if unique and request.action in OUTBOUND_ACTIONS:
            await AuditTrail(uow.audit).record(
                actor=SYSTEM_POLICY,
                action="policy.blocked",
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                correlation_id=request.correlation_id,
                new_state={"action": request.action, "block_codes": [c.value for c in unique]},
                policy_version_id=version.id,
            )
        return decision

    @staticmethod
    def _campaign_blocks(campaign: Campaign, action: str) -> bool:
        if action in OUTBOUND_ACTIONS:
            return campaign.status is not CampaignStatus.ACTIVE
        return campaign.status in (CampaignStatus.PAUSED, CampaignStatus.ARCHIVED)

    @staticmethod
    async def _prospect_domain_id(uow: UnitOfWork, request: PolicyCheckRequest) -> UUID | None:
        if request.subject_type != "prospect":
            return None
        prospect = await uow.prospects.get(request.subject_id)
        if prospect is None:
            raise EntityNotFound("prospect", request.subject_id)
        if prospect.campaign_id != request.campaign_id:
            raise InvalidInput("prospect does not belong to the requested campaign")
        return prospect.domain_id

    @staticmethod
    async def _is_suppressed(
        uow: UnitOfWork, request: PolicyCheckRequest, domain_id: UUID | None, detail: dict[str, Any]
    ) -> bool:
        ctx = request.context
        domain = ctx.get("domain")
        if domain is None and domain_id is not None:
            row = await uow.domains.get(domain_id)
            domain = row.registered_domain if row else None
        result = await check_suppression(
            uow,
            campaign_id=request.campaign_id,
            email=ctx.get("email"),
            domain=domain,
            contact_id=ctx.get("contact_id"),
        )
        if result.suppressed:
            detail["suppressed_scopes"] = list(result.scopes)
        return result.suppressed

    async def _send_gates(
        self,
        uow: UnitOfWork,
        request: PolicyCheckRequest,
        version: PolicyVersion,
        codes: list[BlockCode],
        detail: dict[str, Any],
    ) -> None:
        # Fail closed: an unstated delivery mode is treated as real.
        real = request.context.get("delivery_mode", "real") != "mock"
        if real and not self._settings.real_send_enabled:
            codes.append(BlockCode.REAL_SEND_DISABLED)
        elif real and not await self._approved(
            uow, "campaign", request.campaign_id, ApprovalKind.LIVE_SEND_ACTIVATION
        ):
            codes.append(BlockCode.APPROVAL_REQUIRED)
            detail["approval_kind"] = ApprovalKind.LIVE_SEND_ACTIVATION.value
        if version.body.require_approval_for_send and not await self._approved(
            uow, request.subject_type, request.subject_id, ApprovalKind.MESSAGE_SEND
        ):
            codes.append(BlockCode.APPROVAL_REQUIRED)
            detail.setdefault("approval_kind", ApprovalKind.MESSAGE_SEND.value)

    async def _commitment_gate(
        self,
        uow: UnitOfWork,
        request: PolicyCheckRequest,
        version: PolicyVersion,
        codes: list[BlockCode],
    ) -> None:
        if version.body.require_approval_for_commitments and not await self._approved(
            uow, request.subject_type, request.subject_id, ApprovalKind.NEGOTIATION_COMMITMENT
        ):
            codes.append(BlockCode.APPROVAL_REQUIRED)

    @staticmethod
    async def _approved(
        uow: UnitOfWork, subject_type: str, subject_id: UUID, kind: ApprovalKind
    ) -> bool:
        latest = await uow.approvals.latest_for_subject(
            subject_type=subject_type, subject_id=subject_id, kind=kind.value
        )
        return latest is not None and latest.status is ApprovalStatus.APPROVED

    @staticmethod
    async def _eligibility(
        uow: UnitOfWork,
        version: PolicyVersion,
        domain_id: UUID,
        codes: list[BlockCode],
        detail: dict[str, Any],
    ) -> None:
        latest = await uow.domain_metrics.latest(domain_id, metrics_to_fetch(version.body))
        ev = evaluate(version.body, latest)
        detail["qualification_outcome"] = ev.outcome.value
        detail["reason_codes"] = list(ev.reason_codes)
        if ev.missing_metrics:
            codes.append(BlockCode.METRIC_MISSING)
            detail["missing_metrics"] = list(ev.missing_metrics)
        if ev.threshold_failures:
            codes.append(BlockCode.HARD_THRESHOLD_FAILED)
