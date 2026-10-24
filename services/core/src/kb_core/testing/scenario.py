"""Scenario builder for service tests. Seeds state straight through the fake repositories
(bypassing the services under test, which is the point: setup must not depend on them)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID

from kb_contracts.config import Settings
from kb_contracts.domain import Campaign, PolicyBody, Prospect
from kb_contracts.enums import CampaignStatus, ProspectStatus

from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.qualification.evaluator import METRIC_DA, METRIC_SPAM
from kb_core.qualification.service import QualificationService
from kb_core.testing.outreach_fakes import OutreachStore, OutreachUowFactory
from kb_core.transitions.service import TransitionService


@dataclass
class World:
    campaign: Campaign
    prospect: Prospect
    channel: Any
    sequence: Any
    message: Any


class Scenario:
    def __init__(self, body: PolicyBody | None = None, *, settings: Settings | None = None) -> None:
        self.factory = OutreachUowFactory()
        self.store: OutreachStore = self.factory.store
        self.policy_version_id: UUID = self.store.add_policy(body)
        self.settings = settings or Settings(_env_file=None)
        self.policy = DeterministicPolicyEngine(self.factory, self.settings, self.store.clock)
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

    # -- outreach helpers (Wave 2A) ----------------------------------------------------------
    async def sender(self, campaign: Campaign, *, enabled: bool = True, **over: Any) -> UUID:
        """Give the campaign a usable email sender account (override fields to break it)."""
        from uuid import uuid4

        account = {
            "id": uuid4(),
            "provider_type": "email",
            "provider_name": "mock",
            "enabled": enabled,
            "is_paid": False,
            **over,
        }
        self.store.provider_accounts[account["id"]] = account
        self.store.campaigns[campaign.id] = self.store.campaigns[campaign.id].model_copy(
            update={"sender_provider_account_id": account["id"]}
        )
        return account["id"]  # type: ignore[no-any-return]

    async def channel(self, prospect: Prospect, email: str = "editor@example.com") -> Any:
        async with self.factory() as uow:
            contact = await uow.contacts.create(
                domain_id=prospect.domain_id,
                name=None,
                role=None,
                contact_type="editor",
                confidence=0.9,
            )
            return await uow.contacts.add_channel(
                contact_id=contact.id,
                channel_type="email",
                value_normalized=email,
                confidence=0.9,
            )

    async def sequence(self, prospect: Prospect, channel: Any) -> Any:
        async with self.factory() as uow:
            return await uow.sequences.create(
                campaign_id=prospect.campaign_id,
                prospect_id=prospect.id,
                contact_channel_id=channel.id,
            )

    async def message(
        self, sequence: Any, *, step_no: int = 1, status: Any = None, **kw: Any
    ) -> Any:
        """Create a message and force its status through the fake store (test setup only)."""
        key = kw.pop(
            "idempotency_key", f"idem-{sequence.id}-{step_no}-{kw.get('message_version', 1)}"
        )
        async with self.factory() as uow:
            msg = await uow.messages.create(
                sequence_id=sequence.id,
                step_no=step_no,
                subject="Hello",
                body="Body",
                idempotency_key=key,
                **kw,
            )
        if status is not None:
            msg = msg.model_copy(update={"status": status})
            self.store.messages[msg.id] = msg
        return msg

    async def world(self, status: Any = None, *, sender: bool = True, **msg_kw: Any) -> World:
        """campaign + (sender) + prospect + email channel + sequence + one message at `status`."""
        from kb_contracts.enums import MessageStatus

        c = await self.campaign()
        if sender:
            await self.sender(c)
        p = await self.prospect(c)
        ch = await self.channel(p)
        seq = await self.sequence(p, ch)
        msg = await self.message(
            seq, status=status if status is not None else MessageStatus.APPROVED, **msg_kw
        )
        return World(c, p, ch, seq, msg)
