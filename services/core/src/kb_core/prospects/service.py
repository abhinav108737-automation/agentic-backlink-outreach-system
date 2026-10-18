"""Prospect read use cases. Mutations of status live in the transition/qualification services."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from kb_contracts.domain import Prospect
from kb_contracts.enums import ProspectStatus

from kb_core.db.protocols import Page, UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.prospects.views import ContactView, MetricReading, ProspectDetailView
from kb_core.qualification.evaluator import (
    MetricState,
    classify_metric,
    explain_decision,
    metrics_to_fetch,
)


class ProspectQueryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: ProspectStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[Prospect]:
        async with self._uow_factory() as uow:
            return await uow.prospects.list(
                campaign_id=campaign_id, status=status, cursor=cursor, limit=limit
            )

    async def detail(self, prospect_id: UUID) -> ProspectDetailView:
        async with self._uow_factory() as uow:
            prospect = await uow.prospects.get(prospect_id)
            if prospect is None:
                raise EntityNotFound("prospect", prospect_id)
            domain = await uow.domains.get(prospect.domain_id)
            decision = await uow.qualifications.latest(prospect.id)

            metrics: tuple[MetricReading, ...] = ()
            explanation: str | None = None
            version = await uow.policies.active_version_for_campaign(prospect.campaign_id)
            if version is not None:
                names = metrics_to_fetch(version.body)
                latest = await uow.domain_metrics.latest(prospect.domain_id, names)
                readings: list[MetricReading] = []
                for m in names:
                    state = classify_metric(latest, m)
                    value = latest[m] if state is MetricState.AVAILABLE else None
                    readings.append(MetricReading(metric=m, state=state, value=value))
                metrics = tuple(readings)
            if decision is not None:
                # From the recorded outcome and reason codes, so it matches what was decided.
                explanation = explain_decision(decision.outcome, decision.reason_codes)

            channels_by_contact = defaultdict(list)
            for ch in await uow.contacts.list_channels(prospect.domain_id):
                channels_by_contact[ch.contact_id].append(ch)
            contacts = tuple(
                ContactView(contact=c, channels=tuple(channels_by_contact[c.id]))
                for c in await uow.contacts.list_for_domain(prospect.domain_id)
            )
            return ProspectDetailView(
                prospect=prospect,
                domain=domain,
                qualification=decision,
                qualification_explanation=explanation,
                metrics=metrics,
                ranking=await uow.rankings.latest(prospect.id),
                dossier=await uow.dossiers.latest(prospect.id),
                evidence=tuple(await uow.evidence.list_for_prospect(prospect.id)),
                contacts=contacts,
                urls=tuple(await uow.prospect_urls.list_for_prospect(prospect.id)),
            )
