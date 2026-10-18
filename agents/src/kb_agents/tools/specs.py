"""Typed per-tool argument and result schemas (the vocabulary the ToolGateway validates).

Every tool in ``kb_contracts.tools.TOOL_ALLOWLIST`` has an args model here. Capabilities are
injected by the Lead at integration (I1); these models are the seam they must honour.

Conventions: args are strict (``extra="forbid"``), NaN/inf are rejected, and a metric that is
unavailable is ``None`` while a never-fetched metric is simply absent (ADR-0015); neither is
ever coerced to zero.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from kb_contracts.agents import (
    ContactCandidates,
    DiscoveryResult,
    DossierDraft,
    DraftMessage,
    NegotiationPlan,
    QualificationAssessment,
    ReplyClassificationResult,
    VerificationAmbiguityResult,
    WeeklyNarrative,
)
from kb_contracts.enums import AgentName
from kb_contracts.providers import FetchedPage, SearchResult
from pydantic import BaseModel, ConfigDict, Field, field_validator

Finite = Annotated[float, Field(allow_inf_nan=False)]


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


# ------------------------------------------------------------------------------ args


class ReadCampaignStateArgs(ToolModel):
    campaign_id: UUID


class ReadPolicyArgs(ToolModel):
    campaign_id: UUID | None = None


class ReadMemoryArgs(ToolModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class ReadProviderHealthArgs(ToolModel):
    provider: str | None = Field(default=None, max_length=100)


class DelegateTaskArgs(ToolModel):
    campaign_id: UUID
    agent: AgentName
    description: str = Field(min_length=1, max_length=1000)
    subject_id: UUID | None = None


class RequestSendArgs(ToolModel):
    draft_id: UUID
    rationale: str = Field(min_length=1, max_length=1000)


class RequestFollowupArgs(ToolModel):
    opportunity_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


class RequestVerificationArgs(ToolModel):
    backlink_id: UUID


class RequestReportArgs(ToolModel):
    campaign_id: UUID
    week_start: date | None = None


class ProposeExperimentArgs(ToolModel):
    campaign_id: UUID
    hypothesis: str = Field(min_length=1, max_length=2000)
    parameter: str = Field(min_length=1, max_length=200)


class EscalateToHumanArgs(ToolModel):
    subject_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class SearchArgs(ToolModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=10, ge=1, le=20)


class RecordCandidatesArgs(ToolModel):
    campaign_id: UUID
    result: DiscoveryResult


class ReadDomainMetricsArgs(ToolModel):
    domain: str = Field(min_length=1, max_length=253)
    metrics: tuple[str, ...] = Field(min_length=1, max_length=20)


class RecordAssessmentArgs(ToolModel):
    campaign_id: UUID | None = None
    assessment: QualificationAssessment


class FetchPageArgs(ToolModel):
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("only http(s) URLs may be fetched")
        return v


class EvidenceInput(ToolModel):
    source_url: str = Field(max_length=2048)
    excerpt: str = Field(min_length=1, max_length=1000)
    content_hash: str = Field(min_length=64, max_length=64)
    kind: str = Field(min_length=1, max_length=64)


class RecordEvidenceArgs(ToolModel):
    prospect_id: UUID
    items: tuple[EvidenceInput, ...] = Field(max_length=100)


class RecordDossierArgs(ToolModel):
    dossier: DossierDraft


class ReadEvidenceArgs(ToolModel):
    prospect_id: UUID
    limit: int = Field(default=100, ge=1, le=500)


class RecordContactCandidatesArgs(ToolModel):
    candidates: ContactCandidates


class RecordDraftArgs(ToolModel):
    draft: DraftMessage


class ReadThreadArgs(ToolModel):
    thread_ref: str = Field(min_length=1, max_length=500)


class RecordClassificationArgs(ToolModel):
    thread_ref: str = Field(min_length=1, max_length=500)
    result: ReplyClassificationResult


class RecordNegotiationPlanArgs(ToolModel):
    plan: NegotiationPlan


class ReadBacklinkArgs(ToolModel):
    backlink_id: UUID


class RecordVerificationAssessmentArgs(ToolModel):
    result: VerificationAmbiguityResult


class ReadReportSnapshotArgs(ToolModel):
    campaign_id: UUID
    week_start: date | None = None


class RecordNarrativeArgs(ToolModel):
    narrative: WeeklyNarrative


# ---------------------------------------------------------------------------- results
# Result models are defined for the tools the four Wave-1 agents consume. The gateway
# validates capability output against them; invalid output is a failed call, never coerced.


class CampaignStateView(ToolModel):
    campaign_id: UUID
    name: str = ""
    topics: tuple[str, ...] = ()
    seed_queries: tuple[str, ...] = ()
    status: str = ""  # read-only: agents never write status columns


class PolicyView(ToolModel):
    policy_version: str
    required_metrics: tuple[str, ...] = ()
    campaign_topics: tuple[str, ...] = ()


class MemorySnippet(ToolModel):
    text: str
    source_ref: str = ""
    score: Finite | None = None


class MemoryData(ToolModel):
    snippets: tuple[MemorySnippet, ...] = ()


class SearchData(ToolModel):
    provider: str
    results: tuple[SearchResult, ...] = ()


class FetchPageData(ToolModel):
    page: FetchedPage


class DomainMetricsView(ToolModel):
    """``metrics[name]`` float = fetched; ``None`` = fetched but unavailable; absent = never
    fetched. The last two both mean the metric is missing; neither is ever zero."""

    domain: str
    metrics: dict[str, Finite | None] = Field(default_factory=dict)


class EvidenceView(ToolModel):
    evidence_id: UUID
    source_url: str
    excerpt: str
    content_hash: str
    kind: str


class EvidenceListData(ToolModel):
    items: tuple[EvidenceView, ...] = ()


class EvidenceRef(ToolModel):
    content_hash: str
    evidence_id: UUID


class RecordEvidenceAck(ToolModel):
    refs: tuple[EvidenceRef, ...] = ()
    duplicate: bool = False


class RecordAck(ToolModel):
    record_id: UUID | None = None
    duplicate: bool = False


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    args_model: type[ToolModel]
    result_model: type[ToolModel] | None = None
    mutating: bool = False  # record_* / request_* / delegate / propose / escalate


def _spec(
    name: str,
    args: type[ToolModel],
    result: type[ToolModel] | None = None,
    *,
    mutating: bool = False,
) -> ToolSpec:
    return ToolSpec(name=name, args_model=args, result_model=result, mutating=mutating)


TOOL_SPECS: dict[str, ToolSpec] = {
    s.name: s
    for s in (
        _spec("read_campaign_state", ReadCampaignStateArgs, CampaignStateView),
        _spec("read_policy", ReadPolicyArgs, PolicyView),
        _spec("read_memory", ReadMemoryArgs, MemoryData),
        _spec("read_provider_health", ReadProviderHealthArgs),
        _spec("delegate_task", DelegateTaskArgs, RecordAck, mutating=True),
        _spec("request_send", RequestSendArgs, RecordAck, mutating=True),
        _spec("request_followup", RequestFollowupArgs, RecordAck, mutating=True),
        _spec("request_verification", RequestVerificationArgs, RecordAck, mutating=True),
        _spec("request_report", RequestReportArgs, RecordAck, mutating=True),
        _spec("propose_experiment", ProposeExperimentArgs, RecordAck, mutating=True),
        _spec("escalate_to_human", EscalateToHumanArgs, RecordAck, mutating=True),
        _spec("search", SearchArgs, SearchData),
        _spec("record_candidates", RecordCandidatesArgs, RecordAck, mutating=True),
        _spec("read_domain_metrics", ReadDomainMetricsArgs, DomainMetricsView),
        _spec("record_assessment", RecordAssessmentArgs, RecordAck, mutating=True),
        _spec("fetch_page", FetchPageArgs, FetchPageData),
        _spec("record_evidence", RecordEvidenceArgs, RecordEvidenceAck, mutating=True),
        _spec("record_dossier", RecordDossierArgs, RecordAck, mutating=True),
        _spec("read_evidence", ReadEvidenceArgs, EvidenceListData),
        _spec("record_contact_candidates", RecordContactCandidatesArgs, RecordAck, mutating=True),
        _spec("record_draft", RecordDraftArgs, RecordAck, mutating=True),
        _spec("read_thread", ReadThreadArgs),
        _spec("record_classification", RecordClassificationArgs, RecordAck, mutating=True),
        _spec("record_negotiation_plan", RecordNegotiationPlanArgs, RecordAck, mutating=True),
        _spec("read_backlink", ReadBacklinkArgs),
        _spec(
            "record_verification_assessment",
            RecordVerificationAssessmentArgs,
            RecordAck,
            mutating=True,
        ),
        _spec("read_report_snapshot", ReadReportSnapshotArgs),
        _spec("record_narrative", RecordNarrativeArgs, RecordAck, mutating=True),
    )
}

Payload = dict[str, Any]
