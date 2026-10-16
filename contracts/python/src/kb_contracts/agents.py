"""C3: agent input/output contracts. Outputs are Pydantic-validated; invalid output = failure."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from kb_contracts.domain import DTO
from kb_contracts.enums import AgentName, OperatorAction, ReplyClass
from kb_contracts.tools import TypedToolRequest


class OperatorDecision(DTO):
    decision_id: UUID
    campaign_id: UUID
    action: OperatorAction
    subject_id: UUID | None = None
    rationale: str
    evidence_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0, le=1)
    policy_version: str
    tool_request: TypedToolRequest | None = None
    requires_human_approval: bool = False


class PlanTask(DTO):
    task_id: str
    agent: AgentName
    description: str
    depends_on: tuple[str, ...] = ()
    priority: int = 0
    stop_condition: str | None = None


class TaskPlan(DTO):
    campaign_id: UUID
    tasks: tuple[PlanTask, ...]


class Provenance(DTO):
    source: str
    query: str | None = None
    url: str | None = None


class CandidateDomain(DTO):
    domain: str
    urls: tuple[str, ...] = ()
    provenance: Provenance


class DiscoveryResult(DTO):
    candidates: tuple[CandidateDomain, ...]


class QualificationAssessment(DTO):
    """Advisory only: hard eligibility is deterministic (PolicyEngine)."""

    prospect_id: UUID
    relevance: float = Field(ge=0, le=1)
    editorial_fit: float = Field(ge=0, le=1)
    notes: str = ""
    evidence_ids: tuple[UUID, ...] = ()


class DossierDraft(DTO):
    prospect_id: UUID
    summary: str
    topics: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    unsupported_claims: tuple[str, ...] = ()


class ContactCandidate(DTO):
    name: str | None = None
    role: str | None = None
    channel_type: str
    value: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = ()


class ContactCandidates(DTO):
    prospect_id: UUID
    candidates: tuple[ContactCandidate, ...]


class DraftClaim(DTO):
    text: str
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)  # unsupported claims are unrepresentable


class DraftMessage(DTO):
    sequence_id: UUID
    step_no: int
    subject: str
    body: str
    claims: tuple[DraftClaim, ...] = ()
    qa_score: float | None = Field(default=None, ge=0, le=1)


class ReplyClassificationResult(DTO):
    reply_class: ReplyClass
    confidence: float = Field(ge=0, le=1)
    next_action: str
    return_date: str | None = None


class NegotiationPlan(DTO):
    opportunity_id: UUID
    draft_response: str
    recommendation: str
    risk_level: str = Field(pattern="^(low|medium|high)$")
    makes_binding_commitment: bool = False


class VerificationAmbiguityResult(DTO):
    backlink_id: UUID
    assessment: str
    needs_human: bool = False


class WeeklyNarrative(DTO):
    """Narrative only; numbers come from deterministic snapshots (docs/03)."""

    snapshot_hash: str
    narrative: str
    referenced_metrics: tuple[str, ...] = ()


class AgentRunRecord(DTO):
    agent_run_id: UUID
    campaign_id: UUID | None = None
    agent: AgentName
    task_type: str
    input_refs: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    provider: str | None = None
    prompt_version: str | None = None
    skill_version: str | None = None
    tool_calls: tuple[str, ...] = ()
    structured_result: dict[str, Any] | None = None
    confidence: float | None = None
    policy_version: str | None = None
    latency_ms: int | None = None
    failure: str | None = None
    fallback_used: bool = False
