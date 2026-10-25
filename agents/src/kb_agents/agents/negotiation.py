"""Negotiation agent -> ``NegotiationPlan``. PROPOSES; it never commits.

The agent drafts a response and a recommendation and rates risk. Whether a human must approve is
decided afterwards by ``kb_core.negotiation.service.needs_human`` and the approval row; nothing
this agent returns moves an opportunity, approves a term or sends a message.

``makes_binding_commitment`` is a one-way ratchet:

* the model may raise it to ``true``;
* a ``false`` from the model is never trusted: the draft is also scanned by the local mirror of
  the deterministic commitment detector and by the injected ``commitment_detector`` (the Lead
  passes ``kb_core.negotiation.service.detect_commitment``). If either fires, the flag is raised
  to ``true`` (and risk to at least ``medium``) and the run reports it. A downgrade is therefore
  impossible, and the plan is never "safe" because the model said so;
* ``NegotiationService.plan_round`` re-runs ``needs_human`` on the plan regardless.

The task carries the deterministic reply class; classes that are suppressed or closed
(opt-out, bounce, decline, out-of-office, unknown) fail the run before any tool call.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from kb_contracts.agents import NegotiationPlan
from kb_contracts.enums import AgentName, ReplyClass
from kb_core.research.untrusted import looks_like_instruction
from pydantic import Field

from kb_agents.agents.base import ModelOutput, TaskModel, call_ack, call_typed
from kb_agents.authority import (
    NEGOTIABLE_CLASSES,
    NegotiationRecommendation,
    RiskLevel,
    detect_commitment,
    model_safe_text,
)
from kb_agents.model_bridge import ModelSession
from kb_agents.prompts.common import UNTRUSTED_RULES, wrap_untrusted
from kb_agents.prompts.thread import pick_focus, render_thread
from kb_agents.runs.lifecycle import (
    AgentFailure,
    AgentOutcome,
    AgentRuntime,
    BodyResult,
    ToolCallFailed,
    execute_run,
)
from kb_agents.tools.gateway import Toolbox
from kb_agents.tools.specs import (
    MemoryData,
    PolicyView,
    ReadMemoryArgs,
    ReadPolicyArgs,
    ReadThreadArgs,
    RecordNegotiationPlanArgs,
    ThreadView,
)

PROMPT_VERSION = "negotiation.prompt.v1"
SKILL_VERSION = "negotiation.skill@0.1.0"
SYSTEM_PROMPT = (
    "You help a research publisher respond to a website editor who replied to a guest-post "
    "outreach message with terms or questions. Draft a short, polite, professional reply and "
    "recommend a next step from the list you are given. You CANNOT commit the company: never "
    "agree to a price, fee, payment, exclusivity, contract or guarantee; a person decides that "
    "after review. Set makes_binding_commitment to true if your draft says or implies the "
    "company agrees to anything binding. Rate risk as low, medium or high. Give your confidence "
    "between 0 and 1. " + UNTRUSTED_RULES
)
RECOMMENDATIONS = (
    "reply_with_draft, ask_clarifying_question, propose_counter_for_review, "
    "hold_for_human_review, escalate_to_human"
)

CommitmentDetector = Callable[[str], bool]


class NegotiationTask(TaskModel):
    campaign_id: UUID
    opportunity_id: UUID
    thread_ref: str = Field(min_length=1, max_length=500)
    reply_class: ReplyClass  # the deterministic class of the latest reply
    reply_id: str | None = Field(default=None, max_length=100)
    correlation_id: str | None = None


class NegotiationDraft(ModelOutput):
    draft_response: str = Field(min_length=1, max_length=4000)
    recommendation: NegotiationRecommendation
    risk_level: RiskLevel
    makes_binding_commitment: bool
    confidence: float = Field(ge=0, le=1)


class NegotiationAgent:
    agent = AgentName.NEGOTIATION

    def __init__(
        self, runtime: AgentRuntime, *, commitment_detector: CommitmentDetector | None = None
    ) -> None:
        self._rt = runtime
        self._detector = commitment_detector

    async def run(self, task: NegotiationTask) -> AgentOutcome[NegotiationPlan]:
        async def body(toolbox: Toolbox, session: ModelSession) -> BodyResult[NegotiationPlan]:
            return await self._body(task, toolbox, session)

        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="negotiation",
            campaign_id=task.campaign_id,
            input_refs={
                "opportunity_id": str(task.opportunity_id),
                "thread_ref": task.thread_ref,
                "reply_id": task.reply_id,
                "deterministic_class": task.reply_class.value,
            },
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    def _commitment_detected(self, text: str) -> bool:
        if detect_commitment(text):
            return True
        return bool(self._detector is not None and self._detector(text))

    async def _body(
        self, task: NegotiationTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[NegotiationPlan]:
        if task.reply_class not in NEGOTIABLE_CLASSES:  # before any tool call or model call
            raise AgentFailure(
                "reply_class_not_negotiable",
                f"{task.reply_class.value} replies are handled by deterministic services",
            )
        warnings: list[str] = []

        policy = await call_typed(
            toolbox, "read_policy", ReadPolicyArgs(campaign_id=task.campaign_id), PolicyView
        )
        thread = await call_typed(
            toolbox, "read_thread", ReadThreadArgs(thread_ref=task.thread_ref), ThreadView
        )
        rendered = render_thread(thread, pick_focus(thread, task.reply_id))
        if rendered.dropped_segments:
            warnings.append(f"instruction_like_text_removed:{rendered.dropped_segments}")

        memory_block = ""
        try:
            memory = await call_typed(
                toolbox,
                "read_memory",
                ReadMemoryArgs(query=f"negotiation {thread.domain or 'guest post'}", limit=3),
                MemoryData,
            )
            snippets = [model_safe_text(s.text, max_chars=400)[0] for s in memory.snippets]
            memory_block = "\n".join(
                wrap_untrusted(f"K{i + 1}", "memory", text)
                for i, text in enumerate(t for t in snippets if t)
            )
        except ToolCallFailed as exc:
            warnings.append(f"memory_unavailable:{exc}")

        prompt = (
            f"Deterministic reply class: {task.reply_class.value}\n"
            f"Policy version: {policy.policy_version}\n"
            f"Permitted recommendation values: {RECOMMENDATIONS}\n\n"
            f"Thread, oldest first:\n{rendered.text}\n\n"
            f"Related memory:\n{memory_block or 'none'}"
        )
        draft = await session.run_structured(NegotiationDraft, system=SYSTEM_PROMPT, prompt=prompt)

        text = draft.draft_response.strip()
        if looks_like_instruction(text):  # model output must not relay an instruction onward
            raise AgentFailure("instruction_like_output", "the draft contains instruction text")

        binding = draft.makes_binding_commitment
        risk: RiskLevel = draft.risk_level
        if not binding and self._commitment_detected(text):
            binding = True  # the ratchet: a model's `false` is never trusted
            warnings.append("commitment_detected:flag_raised")
        if self._commitment_detected(text) and risk == "low":
            risk = "medium"
            warnings.append("commitment_detected:risk_raised")
        if binding:
            warnings.append("requires_human_approval")

        plan = NegotiationPlan(
            opportunity_id=task.opportunity_id,
            draft_response=text,
            recommendation=draft.recommendation,
            risk_level=risk,
            makes_binding_commitment=binding,
        )
        await call_ack(toolbox, "record_negotiation_plan", RecordNegotiationPlanArgs(plan=plan))
        return BodyResult(output=plan, confidence=draft.confidence, warnings=tuple(warnings))
