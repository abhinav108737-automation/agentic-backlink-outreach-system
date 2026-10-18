"""Scenario builder for service tests. Seeds state straight through the fake repositories
(bypassing the services under test, which is the point: setup must not depend on them)."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from kb_contracts.config import Settings
from kb_contracts.domain import Campaign, PolicyBody, Prospect
from kb_contracts.enums import CampaignStatus, ProspectStatus

from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.qualification.evaluator import METRIC_DA, METRIC_SPAM
from kb_core.qualification.service import QualificationService
from kb_core.testing.fakes import FakeStore, FakeUowFactory
from kb_core.transitions.service import TransitionService


class Scenario:
    def __init__(self, body: PolicyBody | None = None, *, settings: Settings | None = None) -> None:
        self.factory = FakeUowFactory()
        self.store: FakeStore = self.factory.store
        self.policy_version_id: UUID = self.store.add_policy(body)
        self.settings = settings or Settings(_env_file=None)
        self.policy = DeterministicPolicyEngine(self.factory, self.settings)
        self.transitions = TransitionService(self.factory, self.store.clock)
        self.qualification = QualificationService(self.factory, self.policy, self.transitions)

    async def campaign(self, status: CampaignStatus = CampaignStatus.ACTIVE) -> Campaign:
        async with self.factory() as uow:
            c: Campaign = await uow.campaigns.create(name="c")
            if status is not CampaignStatus.DRAFT:
                c = await uow.campaigns.set_status(c.id, status.value, expected_version=c.version)
            return c

    async def prospect(
        self,
        campaign: Campaign,
        domain: str = "example.com",
        status: ProspectStatus = ProspectStatus.DISCOVERED,
    ) -> Prospect:
        async with self.factory() as uow:
            d = await uow.domains.get_or_create(domain)
            p: Prospect = await uow.prospects.create(campaign_id=campaign.id, domain_id=d.id)
            if status is not ProspectStatus.DISCOVERED:
                p = await uow.prospects.set_status(p.id, status, expected_version=p.version)
            return p

    async def metric(self, prospect: Prospect, metric: str, value: float | None) -> None:
        async with self.factory() as uow:
            await uow.domain_metrics.record(
                domain_id=prospect.domain_id,
                provider="test",
                metric=metric,
                value=value,
                source="test",
                fetched_at=self.store.clock.now().astimezone(UTC),
            )

    async def good_metrics(self, prospect: Prospect, da: float = 60.0, spam: float = 0.2) -> None:
        await self.metric(prospect, METRIC_DA, da)
        await self.metric(prospect, METRIC_SPAM, spam)

    async def refresh(self, prospect: Prospect) -> Prospect:
        async with self.factory() as uow:
            fresh: Prospect | None = await uow.prospects.get(prospect.id)
            assert fresh is not None
            return fresh
