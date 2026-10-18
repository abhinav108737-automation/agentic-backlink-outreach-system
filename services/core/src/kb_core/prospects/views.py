"""Read models for the prospect screens. Reuse the frozen C2 DTOs; add only the explanation."""

from __future__ import annotations

from kb_contracts.domain import (
    DTO,
    Contact,
    ContactChannel,
    Domain,
    EvidenceItem,
    Prospect,
    ProspectUrl,
    QualificationDecision,
    RankingResult,
    ResearchDossier,
)
from pydantic import Field

from kb_core.qualification.evaluator import MetricState


class MetricReading(DTO):
    """One required metric's current state. `value` is None unless `state == available`; an
    unavailable or never-fetched metric is `null` on the wire, never 0 and never NaN."""

    metric: str
    state: MetricState
    value: float | None = Field(default=None, allow_inf_nan=False)


class ContactView(DTO):
    contact: Contact
    channels: tuple[ContactChannel, ...] = ()


class ProspectDetailView(DTO):
    prospect: Prospect
    domain: Domain | None = None
    qualification: QualificationDecision | None = None
    qualification_explanation: str | None = None
    metrics: tuple[MetricReading, ...] = ()
    ranking: RankingResult | None = None
    dossier: ResearchDossier | None = None
    evidence: tuple[EvidenceItem, ...] = ()
    contacts: tuple[ContactView, ...] = ()
    urls: tuple[ProspectUrl, ...] = ()
