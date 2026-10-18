"""Campaign use cases. Status changes go through `TransitionService`, never `set_status`."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from kb_contracts.domain import Campaign
from kb_contracts.enums import CampaignStatus
from kb_contracts.events import Actor

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import Page, UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound, InvalidInput
from kb_core.idempotency.service import IdempotencyService
from kb_core.transitions.service import TransitionResult, TransitionService

_SCOPE = "campaign.create"


class CampaignService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        transitions: TransitionService,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._transitions = transitions
        self._idem = idempotency or IdempotencyService()

    async def create(
        self,
        *,
        name: str,
        goals: dict[str, Any] | None,
        policy_version_id: UUID | None,
        actor: Actor,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[Campaign, bool]:
        """Returns `(campaign, replayed)`. With a key, a repeat returns the original campaign."""
        clean = name.strip()
        if not clean:
            raise InvalidInput("campaign name must not be empty")
        async with self._uow_factory() as uow:

            async def op() -> str:
                created = await self._create_in(
                    uow, clean, goals, policy_version_id, actor, correlation_id
                )
                return f"campaign:{created.id}"

            if idempotency_key is None:
                created = await self._create_in(
                    uow, clean, goals, policy_version_id, actor, correlation_id
                )
                return created, False
            outcome = await self._idem.execute(
                uow,
                scope=_SCOPE,
                key=idempotency_key,
                payload={
                    "name": clean,
                    "goals": goals or {},
                    "policy_version_id": str(policy_version_id) if policy_version_id else None,
                },
                operation=op,
            )
            campaign = await uow.campaigns.get(UUID(outcome.ref.split(":", 1)[1]))
            if campaign is None:  # pragma: no cover - the ref was written in the same history
                raise EntityNotFound("campaign", outcome.ref)
            return campaign, outcome.replayed

    @staticmethod
    async def _create_in(
        uow: UnitOfWork,
        name: str,
        goals: dict[str, Any] | None,
        policy_version_id: UUID | None,
        actor: Actor,
        correlation_id: str,
    ) -> Campaign:
        if (
            policy_version_id is not None
            and await uow.policies.get_version(policy_version_id) is None
        ):
            raise InvalidInput(f"unknown policy_version_id {policy_version_id}")
        created = await uow.campaigns.create(
            name=name, policy_version_id=policy_version_id, goals=goals
        )
        await AuditTrail(uow.audit).record(
            actor=actor,
            action="campaign.created",
            subject_type="campaign",
            subject_id=created.id,
            correlation_id=correlation_id,
            new_state={"name": created.name, "status": created.status.value},
            policy_version_id=policy_version_id,
        )
        return created

    async def get(self, campaign_id: UUID) -> Campaign:
        async with self._uow_factory() as uow:
            found = await uow.campaigns.get(campaign_id)
            if found is None:
                raise EntityNotFound("campaign", campaign_id)
            return found

    async def list(self, *, cursor: str | None = None, limit: int = 50) -> Page[Campaign]:
        async with self._uow_factory() as uow:
            return await uow.campaigns.list(cursor=cursor, limit=limit)

    async def _move(
        self,
        campaign_id: UUID,
        to: CampaignStatus,
        *,
        actor: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> Campaign:
        async with self._uow_factory() as uow:
            current = await uow.campaigns.get(campaign_id)
            if current is None:
                raise EntityNotFound("campaign", campaign_id)
            result: TransitionResult = await self._transitions.transition_in(
                uow,
                current,
                to,
                actor=actor,
                reason=reason or f"campaign {to.value.lower()} requested",
                correlation_id=correlation_id,
            )
            campaign: Campaign = result.entity
            return campaign

    async def pause(
        self, campaign_id: UUID, *, actor: Actor, reason: str | None, correlation_id: str
    ) -> Campaign:
        return await self._move(
            campaign_id,
            CampaignStatus.PAUSED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    async def activate(
        self, campaign_id: UUID, *, actor: Actor, reason: str | None, correlation_id: str
    ) -> Campaign:
        return await self._move(
            campaign_id,
            CampaignStatus.ACTIVE,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )
