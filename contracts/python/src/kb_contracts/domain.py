"""C2: domain DTOs for the aggregates in docs/04. Boundary models only (no ORM)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from kb_contracts.enums import (
    ApprovalKind,
    ApprovalStatus,
    AutonomyMode,
    BacklinkStatus,
    CampaignStatus,
    DeliveryMode,
    JobStatus,
    MemoryType,
    MessageStatus,
    OpportunityStatus,
    OptimizationStatus,
    ProspectStatus,
    QualificationOutcome,
    ReplyClass,
    RiskClass,
    SequenceStatus,
    SuppressionReason,
    SuppressionScope,
    WorkflowStatus,
)


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class Entity(DTO):
    id: UUID
    created_at: datetime
    updated_at: datetime


class Versioned(Entity):
    version: int = Field(ge=1)


class PolicyBody(DTO):
    """Body of an immutable policy_versions row. DA > min_da and Spam < max_spam are strict."""

    min_da: float = 45.0
    max_spam: float = 1.0
    required_metrics: tuple[str, ...] = ("domain_authority", "spam_score")
    on_missing_metric: str = Field(default="defer", pattern="^(defer|reject)$")  # never pass
    ranking_weights: dict[str, float] = Field(default_factory=dict)
    daily_send_limit: int = Field(default=25, ge=0)
    per_domain_send_limit: int = Field(default=1, ge=0)
    per_contact_send_limit: int = Field(default=1, ge=0)
    max_follow_ups: int = Field(default=2, ge=0)
    require_approval_for_send: bool = True
    require_approval_for_commitments: bool = True


class PolicyVersion(Entity):
    policy_id: UUID
    version_no: int
    body: PolicyBody
    content_hash: str
    created_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    source_proposal_id: UUID | None = None


class Campaign(Versioned):
    name: str
    status: CampaignStatus
    autonomy_mode: AutonomyMode = AutonomyMode.SHADOW
    policy_version_id: UUID | None = None
    sender_provider_account_id: UUID | None = None
    goals: dict[str, Any] = Field(default_factory=dict)


class CampaignTarget(Entity):
    campaign_id: UUID
    target_url: str
    topic: str | None = None
    keywords: tuple[str, ...] = ()
    priority: int = 0


class Domain(Entity):
    registered_domain: str
    first_seen_at: datetime


class DomainMetric(Entity):
    domain_id: UUID
    provider: str
    metric: str
    value: float
    fetched_at: datetime
    source: str


class Prospect(Versioned):
    campaign_id: UUID
    domain_id: UUID
    status: ProspectStatus
    discovered_via: str | None = None


class ProspectUrl(Entity):
    prospect_id: UUID
    url: str
    canonical_url: str
    url_hash: str
    page_type: str = "other"
    provenance: dict[str, Any] = Field(default_factory=dict)


class QualificationDecision(Entity):
    prospect_id: UUID
    policy_version_id: UUID
    outcome: QualificationOutcome
    reason_codes: tuple[str, ...] = ()
    metric_snapshot: dict[str, float | None] = Field(default_factory=dict)
    missing_metrics: tuple[str, ...] = ()


class RankingResult(Entity):
    prospect_id: UUID
    policy_version_id: UUID
    score: float
    components: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""


class ResearchDossier(Entity):
    prospect_id: UUID
    version_no: int
    summary: str
    topics: tuple[str, ...] = ()
    editorial_signals: dict[str, Any] = Field(default_factory=dict)
    agent_run_id: UUID | None = None


class EvidenceItem(Entity):
    prospect_id: UUID
    dossier_id: UUID | None = None
    source_url: str
    excerpt: str
    content_hash: str
    kind: str
    extracted_at: datetime
    extractor: str = "deterministic"


class Contact(Entity):
    domain_id: UUID
    name: str | None = None
    role: str | None = None
    contact_type: str = "generic"
    confidence: float = Field(ge=0, le=1)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ContactChannel(Entity):
    contact_id: UUID
    channel_type: str
    value_normalized: str
    verification_status: str = "unverified"
    confidence: float = Field(ge=0, le=1)
    evidence_item_id: UUID | None = None


class SuppressionEntry(Entity):
    scope: SuppressionScope
    value_normalized: str
    reason: SuppressionReason
    campaign_id: UUID | None = None  # None = global
    superseded_at: datetime | None = None


class OutreachSequence(Versioned):
    campaign_id: UUID
    prospect_id: UUID
    contact_channel_id: UUID
    status: SequenceStatus
    current_step: int = 0
    next_due_at: datetime | None = None
    policy_version_id: UUID | None = None


class OutreachMessage(Versioned):
    sequence_id: UUID
    thread_id: UUID | None = None
    step_no: int
    message_version: int
    status: MessageStatus
    subject: str
    body: str
    delivery_mode: DeliveryMode = DeliveryMode.MOCK
    idempotency_key: str
    claims: dict[str, tuple[UUID, ...]] = Field(default_factory=dict)  # claim -> evidence ids
    qa_result: dict[str, Any] = Field(default_factory=dict)


class Reply(Entity):
    sequence_id: UUID
    provider_message_id: str
    from_normalized: str
    received_at: datetime
    body_sanitized: str
    raw_hash: str


class ReplyClassification(Entity):
    reply_id: UUID
    reply_class: ReplyClass
    confidence: float = Field(ge=0, le=1)
    next_action: str
    deterministic_rule_applied: str | None = None
    return_date: datetime | None = None


class GuestPostOpportunity(Versioned):
    prospect_id: UUID
    sequence_id: UUID | None = None
    status: OpportunityStatus
    target_url: str | None = None
    agreed_terms: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class Backlink(Versioned):
    opportunity_id: UUID | None = None
    source_url: str
    target_url: str
    expected_anchor: str | None = None
    status: BacklinkStatus
    rel: tuple[str, ...] = ()
    consecutive_misses: int = 0


class BacklinkVerification(Entity):
    backlink_id: UUID
    checked_at: datetime
    http_status: int | None = None
    final_url: str | None = None
    canonical_url: str | None = None
    link_found: bool
    anchor_text: str | None = None
    rel_attrs: tuple[str, ...] = ()
    result: str


class WorkflowRun(Entity):
    campaign_id: UUID | None = None
    workflow_type: str
    status: WorkflowStatus
    current_step: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str


class ScheduledJob(Entity):
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    run_at: datetime
    status: JobStatus
    attempts: int = 0
    max_attempts: int = 5
    idempotency_key: str
    concurrency_key: str | None = None


class ApprovalRequest(Entity):
    kind: ApprovalKind
    subject_type: str
    subject_id: UUID
    status: ApprovalStatus
    requested_by: str
    decided_by: str | None = None
    reason: str | None = None


class OptimizationProposal(Versioned):
    target_kind: str
    risk_class: RiskClass
    status: OptimizationStatus
    candidate: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)
    evaluation_results: dict[str, Any] = Field(default_factory=dict)
    approval_request_id: UUID | None = None


class Memory(Entity):
    memory_type: MemoryType
    subject_type: str
    subject_id: UUID | None = None
    content: str
    source_refs: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


class ReportSnapshot(Entity):
    payload: dict[str, Any]
    content_hash: str
    source_watermark: str
