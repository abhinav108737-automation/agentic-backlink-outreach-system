"""Contact agent -> ``ContactCandidates`` (tools: fetch_page, read_evidence,
record_contact_candidates).

Candidates, values and confidences come ONLY from Lane E's deterministic ``extract_contacts``.
The model may annotate the *role* of a value that already exists; it cannot add, remove or
re-score a candidate. Annotations naming an unknown value are discarded and reported.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from kb_contracts.agents import ContactCandidate, ContactCandidates
from kb_contracts.enums import AgentName
from kb_core.contacts.extract import ExtractedContact, extract_contacts, to_contact_candidates
from pydantic import Field

from kb_agents.agents.base import ModelOutput, TaskModel, call_ack, call_typed
from kb_agents.model_bridge import ModelSession
from kb_agents.prompts.common import UNTRUSTED_RULES, wrap_untrusted
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
    EvidenceListData,
    FetchPageArgs,
    FetchPageData,
    ReadEvidenceArgs,
    RecordContactCandidatesArgs,
)

PROMPT_VERSION = "contact.prompt.v1"
SKILL_VERSION = "contact.skill@0.1.0"
MAX_PAGES = 5
SYSTEM_PROMPT = (
    "You classify the role of already-extracted contact addresses (editor, partnerships, "
    "press, general, other) from the short context shown. You cannot add or change addresses. "
    + UNTRUSTED_RULES
)

Role = Literal["editor", "partnerships", "press", "general", "other"]


class ContactTask(TaskModel):
    campaign_id: UUID
    prospect_id: UUID
    urls: tuple[str, ...] = Field(min_length=1, max_length=MAX_PAGES)
    correlation_id: str | None = None


class RoleAnnotation(ModelOutput):
    value: str = Field(min_length=1, max_length=320)
    role: Role


class RoleAnnotations(ModelOutput):
    items: tuple[RoleAnnotation, ...] = Field(max_length=50)


class ContactAgent:
    agent = AgentName.CONTACT

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(self, task: ContactTask) -> AgentOutcome[ContactCandidates]:
        async def body(toolbox: Toolbox, session: ModelSession) -> BodyResult[ContactCandidates]:
            return await self._body(task, toolbox, session)

        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="contact",
            campaign_id=task.campaign_id,
            input_refs={"prospect_id": str(task.prospect_id), "urls": list(task.urls)},
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    async def _body(
        self, task: ContactTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[ContactCandidates]:
        warnings: list[str] = []
        ids_by_hash: dict[str, UUID] = {}
        try:
            known = await call_typed(
                toolbox,
                "read_evidence",
                ReadEvidenceArgs(prospect_id=task.prospect_id),
                EvidenceListData,
            )
            ids_by_hash = {e.content_hash: e.evidence_id for e in known.items}
        except ToolCallFailed as exc:
            warnings.append(f"evidence_unavailable:{exc}")

        best: dict[tuple[str, str], ExtractedContact] = {}
        fetched = 0
        last_failure: ToolCallFailed | None = None
        for url in task.urls:
            try:
                data = await call_typed(
                    toolbox, "fetch_page", FetchPageArgs(url=url), FetchPageData
                )
            except ToolCallFailed as exc:
                last_failure = exc
                warnings.append(f"fetch_failed:{exc}")
                continue
            fetched += 1
            for contact in extract_contacts(data.page):
                key = (contact.channel_type, contact.value)
                held = best.get(key)
                if held is None or contact.confidence > held.confidence:
                    best[key] = contact
        if fetched == 0:
            if last_failure is not None:
                raise last_failure
            raise AgentFailure("no_pages", "no page could be fetched")

        contacts = sorted(best.values(), key=lambda c: (-c.confidence, c.channel_type, c.value))
        roles: dict[str, str] = {}
        unroled = [c for c in contacts if c.role is None and c.channel_type == "email"]
        if unroled:
            blocks = "\n".join(
                wrap_untrusted(
                    f"C{i + 1}", c.evidence.source_url, f"{c.value} :: {c.evidence.excerpt}"
                )
                for i, c in enumerate(unroled)
            )
            annotated = await session.run_structured(
                RoleAnnotations, system=SYSTEM_PROMPT, prompt=f"Classify these:\n{blocks}"
            )
            allowed = {c.value for c in unroled}
            for item in annotated.items:
                value = item.value.strip().lower()
                if value in allowed:
                    roles.setdefault(value, item.role)
                else:
                    warnings.append("annotation_for_unknown_value_discarded")

        base = to_contact_candidates(task.prospect_id, contacts, ids_by_hash)
        merged = tuple(
            ContactCandidate(
                name=c.name,
                role=c.role if c.role is not None else roles.get(c.value),
                channel_type=c.channel_type,
                value=c.value,
                confidence=c.confidence,
                evidence_ids=c.evidence_ids,
            )
            for c in base.candidates
        )
        result = ContactCandidates(prospect_id=task.prospect_id, candidates=merged)
        if merged:
            await call_ack(
                toolbox, "record_contact_candidates", RecordContactCandidatesArgs(candidates=result)
            )
        confidence = max((c.confidence for c in merged), default=None)
        return BodyResult(output=result, confidence=confidence, warnings=tuple(warnings))
