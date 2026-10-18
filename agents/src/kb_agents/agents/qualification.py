"""Qualification agent -> ``QualificationAssessment``. ADVISORY ONLY.

It never emits pass/reject/defer: hard eligibility and the DEFERRED decision for a missing
metric are deterministic (PolicyEngine). Metrics are read only to annotate ``notes`` with which
required metrics are missing; they are never shown to the model and never become zero/NaN.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.agents import QualificationAssessment
from kb_contracts.enums import AgentName
from kb_core.research.untrusted import looks_like_instruction
from pydantic import Field

from kb_agents.agents.base import ModelOutput, TaskModel, call_ack, call_typed
from kb_agents.model_bridge import ModelSession
from kb_agents.prompts.common import UNTRUSTED_RULES, wrap_untrusted
from kb_agents.runs.lifecycle import (
    AgentFailure,
    AgentOutcome,
    AgentRuntime,
    BodyResult,
    execute_run,
)
from kb_agents.tools.gateway import Toolbox
from kb_agents.tools.specs import (
    DomainMetricsView,
    PolicyView,
    ReadDomainMetricsArgs,
    ReadPolicyArgs,
    RecordAssessmentArgs,
)

PROMPT_VERSION = "qualification.prompt.v1"
SKILL_VERSION = "qualification.skill@0.1.0"
DEFAULT_REQUIRED_METRICS = ("domain_authority", "spam_score")
SYSTEM_PROMPT = (
    "You judge ONLY topical relevance and editorial fit of a website for a research publisher, "
    "using the supplied evidence excerpts. Scores are between 0 and 1. You do NOT decide "
    "eligibility, approval, rejection or deferral. Cite only evidence ids you were given. "
    + UNTRUSTED_RULES
)


class EvidenceExcerptIn(TaskModel):
    evidence_id: UUID
    excerpt: str


class QualificationTask(TaskModel):
    campaign_id: UUID
    prospect_id: UUID
    domain: str
    campaign_topics: tuple[str, ...] = ()
    evidence: tuple[EvidenceExcerptIn, ...] = ()
    correlation_id: str | None = None


class QualificationJudgement(ModelOutput):
    relevance: float = Field(ge=0, le=1)
    editorial_fit: float = Field(ge=0, le=1)
    notes: str = Field(default="", max_length=1000)
    evidence_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0, le=1)


class QualificationAgent:
    agent = AgentName.QUALIFICATION

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(self, task: QualificationTask) -> AgentOutcome[QualificationAssessment]:
        async def body(
            toolbox: Toolbox, session: ModelSession
        ) -> BodyResult[QualificationAssessment]:
            return await self._body(task, toolbox, session)

        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="qualification",
            campaign_id=task.campaign_id,
            input_refs={
                "prospect_id": str(task.prospect_id),
                "domain": task.domain,
                "evidence_ids": [str(e.evidence_id) for e in task.evidence],
            },
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    async def _body(
        self, task: QualificationTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[QualificationAssessment]:
        policy = await call_typed(
            toolbox, "read_policy", ReadPolicyArgs(campaign_id=task.campaign_id), PolicyView
        )
        required = policy.required_metrics or DEFAULT_REQUIRED_METRICS
        metrics = await call_typed(
            toolbox,
            "read_domain_metrics",
            ReadDomainMetricsArgs(domain=task.domain, metrics=tuple(required)),
            DomainMetricsView,
        )
        # absent (never fetched) and None (unavailable) are both "missing"; never zero.
        missing = tuple(m for m in required if metrics.metrics.get(m) is None)

        topics = task.campaign_topics or policy.campaign_topics
        blocks = "\n".join(
            wrap_untrusted(f"E{i + 1}", task.domain, e.excerpt) for i, e in enumerate(task.evidence)
        )
        id_table = "\n".join(f"E{i + 1} = {e.evidence_id}" for i, e in enumerate(task.evidence))
        prompt = (
            f"Domain: {task.domain}\nCampaign topics: {', '.join(topics) or 'unspecified'}\n"
            f"Evidence ids (cite by uuid):\n{id_table or 'none'}\n\n{blocks or 'No evidence.'}"
        )
        judgement = await session.run_structured(
            QualificationJudgement, system=SYSTEM_PROMPT, prompt=prompt
        )
        known = {e.evidence_id for e in task.evidence}
        stray = [i for i in judgement.evidence_ids if i not in known]
        if stray:  # an invented citation is an invalid output, not something to trim away
            raise AgentFailure("ungrounded_evidence", f"{len(stray)} unknown evidence ids")

        warnings: list[str] = []
        notes = judgement.notes.strip()
        if looks_like_instruction(notes):  # free text must not smuggle an instruction onward
            notes = ""
            warnings.append("instruction_like_notes_dropped")
        if missing:
            tag = "metrics_missing(TBD, deterministic policy decides): " + ", ".join(missing)
            notes = f"{notes}\n{tag}" if notes else tag
        assessment = QualificationAssessment(
            prospect_id=task.prospect_id,
            relevance=judgement.relevance,
            editorial_fit=judgement.editorial_fit,
            notes=notes,
            evidence_ids=judgement.evidence_ids,
        )
        await call_ack(
            toolbox,
            "record_assessment",
            RecordAssessmentArgs(campaign_id=task.campaign_id, assessment=assessment),
        )
        return BodyResult(
            output=assessment, confidence=judgement.confidence, warnings=tuple(warnings)
        )
