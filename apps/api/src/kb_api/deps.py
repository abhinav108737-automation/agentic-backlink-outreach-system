"""Dependency wiring: one explicit service container per app instance (no module globals)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from kb_contracts.config import Settings
from kb_contracts.providers import Clock
from kb_core.approvals.service import ApprovalService
from kb_core.campaigns.service import CampaignService
from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.idempotency.service import IdempotencyService
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.prospects.service import ProspectQueryService
from kb_core.qualification.service import QualificationService
from kb_core.ranking.service import RankingService
from kb_core.readback.service import OutreachReadService
from kb_core.scheduling.clock import SystemClock
from kb_core.sending.service import SendService
from kb_core.sending.verify import BacklinkVerifyService
from kb_core.settings.service import SettingsService
from kb_core.transitions.service import TransitionService


@dataclass(frozen=True)
class Services:
    settings: Settings
    uow_factory: UnitOfWorkFactory
    clock: Clock
    policy: DeterministicPolicyEngine
    transitions: TransitionService
    campaigns: CampaignService
    prospects: ProspectQueryService
    approvals: ApprovalService
    system_settings: SettingsService
    qualification: QualificationService
    ranking: RankingService
    send: SendService
    backlink_verify: BacklinkVerifyService
    readback: OutreachReadService


def build_services(
    settings: Settings, uow_factory: UnitOfWorkFactory, clock: Clock | None = None
) -> Services:
    clk = clock or SystemClock()
    policy = DeterministicPolicyEngine(uow_factory, settings, clk)
    transitions = TransitionService(uow_factory, clk)
    approvals = ApprovalService(uow_factory)
    return Services(
        settings=settings,
        uow_factory=uow_factory,
        clock=clk,
        policy=policy,
        transitions=transitions,
        campaigns=CampaignService(uow_factory, transitions, IdempotencyService()),
        prospects=ProspectQueryService(uow_factory),
        approvals=approvals,
        system_settings=SettingsService(uow_factory),
        qualification=QualificationService(uow_factory, policy, transitions),
        ranking=RankingService(uow_factory, transitions),
        send=SendService(uow_factory, policy, transitions, approvals, clk),
        backlink_verify=BacklinkVerifyService(uow_factory, clk),
        readback=OutreachReadService(uow_factory),
    )


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        # An app built without a database (for example the security/handshake tests) cannot
        # serve persistence-backed operations. 503, not 501: the route IS implemented, the
        # dependency is simply absent, and a misconfigured deployment must fail loudly rather
        # than claim the feature does not exist.
        raise HTTPException(status_code=503, detail="Persistence is not configured")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def correlation_id(request: Request) -> str:
    cid: str = getattr(request.state, "correlation_id", "-")
    return cid
