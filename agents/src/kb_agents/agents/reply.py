"""Reply agent -> ``ReplyClassificationResult``. INTERPRETS; it never classifies.

``kb_core.replies.classify.classify_reply`` decides the ``ReplyClass`` before this agent runs, and
the task carries that decision. Un-overridability is structural, in four layers:

1. ``reply_class`` in the final result is copied from the task, never from model output.
2. The model must echo the class; any other value fails the run (``deterministic_class_conflict``)
   and nothing is recorded. It is not corrected, it is a failure.
3. ``suggested_action`` is a closed vocabulary with no suppress/approve/send/transition member,
   further restricted per class (``opt_out`` and ``bounce`` allow only ``no_action``). Anything
   else fails the run. Opt-out, bounce and suppression are performed by deterministic services.
4. ``return_date`` comes only from the deterministic result, and confidence can only be lowered
   (``min`` of model and classifier), never raised.

The reply body is untrusted (ADR-0020): instruction-like spans are removed before a model sees it.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.agents import ReplyClassificationResult
from kb_contracts.enums import AgentName, ReplyClass
from kb_core.research.untrusted import looks_like_instruction
from pydantic import Field

from kb_agents.agents.base import ModelOutput, TaskModel, call_ack, call_typed
from kb_agents.authority import (
    ALLOWED_ACTIONS_BY_CLASS,
    PROTECTED_CLASSES,
    SuggestedReplyAction,
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
    ReadMemoryArgs,
    ReadThreadArgs,
    RecordClassificationArgs,
    ThreadView,
)

PROMPT_VERSION = "reply.prompt.v1"
SKILL_VERSION = "reply.skill@0.1.0"
SYSTEM_PROMPT = (
    "You interpret an email reply to a guest-post outreach message for a research publisher. "
    "The reply has ALREADY been classified by deterministic rules; that classification is final "
    "and you cannot change, soften or contest it. Echo the class you are given in reply_class. "
    "Choose suggested_action only from the list you are given. You cannot suppress, approve, "
    "send, reject or move anything; people and deterministic services do that. Give a short "
    "rationale (one or two sentences) and your confidence between 0 and 1. " + UNTRUSTED_RULES
)


class DeterministicClass(TaskModel):
    """The decision of ``classify_reply``, passed in by the caller. Read-only for the agent."""

    result: ReplyClassificationResult
    rule: str = Field(min_length=1, max_length=100)


class ReplyTask(TaskModel):
    campaign_id: UUID
    thread_ref: str = Field(min_length=1, max_length=500)
    deterministic: DeterministicClass
    reply_id: str | None = Field(default=None, max_length=100)
    correlation_id: str | None = None


class ReplyInterpretation(ModelOutput):
    reply_class: ReplyClass  # must echo the deterministic class; never trusted as a decision
    suggested_action: SuggestedReplyAction
    rationale: str = Field(default="", max_length=400)
    confidence: float = Field(ge=0, le=1)


class ReplyAgent:
    agent = AgentName.REPLY

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(self, task: ReplyTask) -> AgentOutcome[ReplyClassificationResult]:
        async def body(
            toolbox: Toolbox, session: ModelSession
        ) -> BodyResult[ReplyClassificationResult]:
            return await self._body(task, toolbox, session)

        decided = task.deterministic.result
        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="reply_interpretation",
            campaign_id=task.campaign_id,
            input_refs={
                "thread_ref": task.thread_ref,
                "reply_id": task.reply_id,
                "deterministic_class": decided.reply_class.value,
                "deterministic_rule": task.deterministic.rule,
            },
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    async def _body(
        self, task: ReplyTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[ReplyClassificationResult]:
        decided = task.deterministic.result
        allowed = ALLOWED_ACTIONS_BY_CLASS[decided.reply_class]
        warnings: list[str] = []

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
                ReadMemoryArgs(query=f"{decided.reply_class.value} reply handling", limit=3),
                MemoryData,
            )
            snippets = [model_safe_text(s.text, max_chars=400)[0] for s in memory.snippets]
            memory_block = "\n".join(
                wrap_untrusted(f"K{i + 1}", "memory", text)
                for i, text in enumerate(t for t in snippets if t)
            )
        except ToolCallFailed as exc:  # memory is optional context, not a dependency
            warnings.append(f"memory_unavailable:{exc}")

        prompt = (
            f"DETERMINISTIC CLASSIFICATION (final, not yours to change): "
            f"reply_class={decided.reply_class.value} (rule {task.deterministic.rule})\n"
            f"Permitted suggested_action values: {', '.join(sorted(allowed))}\n\n"
            f"Thread, oldest first:\n{rendered.text}\n\n"
            f"Related memory:\n{memory_block or 'none'}"
        )
        interpretation = await session.run_structured(
            ReplyInterpretation, system=SYSTEM_PROMPT, prompt=prompt
        )

        if interpretation.reply_class is not decided.reply_class:
            raise AgentFailure(
                "deterministic_class_conflict",
                f"model returned {interpretation.reply_class.value}, "
                f"rules decided {decided.reply_class.value}",
            )
        if interpretation.suggested_action not in allowed:
            raise AgentFailure(
                "next_action_not_permitted",
                f"{interpretation.suggested_action} is not permitted for "
                f"{decided.reply_class.value}",
            )

        rationale = interpretation.rationale.strip()
        if looks_like_instruction(rationale):
            rationale = ""
            warnings.append("instruction_like_rationale_dropped")
        if rationale:
            warnings.append(f"rationale:{rationale}")
        if decided.reply_class in PROTECTED_CLASSES:
            warnings.append("protected_class:deterministic_services_act")

        result = ReplyClassificationResult(
            reply_class=decided.reply_class,
            confidence=min(interpretation.confidence, decided.confidence),
            next_action=interpretation.suggested_action,
            return_date=decided.return_date,
        )
        await call_ack(
            toolbox,
            "record_classification",
            RecordClassificationArgs(thread_ref=task.thread_ref, result=result),
        )
        return BodyResult(output=result, confidence=result.confidence, warnings=tuple(warnings))
