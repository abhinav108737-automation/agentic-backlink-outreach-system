"""Research agent -> ``DossierDraft`` (tools: fetch_page, read_memory, record_evidence,
record_dossier).

Facts (topics, evidence excerpts) come ONLY from Lane E's deterministic ``extract_research``.
The model may only propose claims that cite evidence labels. The summary is assembled from the
supported claims, so a claim without evidence can only ever land in ``unsupported_claims``.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.agents import DossierDraft
from kb_contracts.enums import AgentName
from kb_core.research.extract import EvidenceExcerpt, ResearchExtraction, extract_research
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
    ToolCallFailed,
    execute_run,
)
from kb_agents.tools.gateway import Toolbox
from kb_agents.tools.specs import (
    EvidenceInput,
    FetchPageArgs,
    FetchPageData,
    MemoryData,
    ReadMemoryArgs,
    RecordDossierArgs,
    RecordEvidenceAck,
    RecordEvidenceArgs,
)

PROMPT_VERSION = "research.prompt.v1"
SKILL_VERSION = "research.skill@0.1.0"
MAX_PAGES = 5
MAX_EVIDENCE = 50
SUMMARY_KINDS = frozenset({"title", "heading", "meta_description"})
SYSTEM_PROMPT = (
    "You write short factual claims about a website for a research publisher. Every claim MUST "
    "cite the evidence labels (E1, E2, ...) that support it; do not state anything the "
    "evidence does not show. " + UNTRUSTED_RULES
)


class ResearchTask(TaskModel):
    campaign_id: UUID
    prospect_id: UUID
    domain: str
    urls: tuple[str, ...] = Field(min_length=1, max_length=MAX_PAGES)
    correlation_id: str | None = None


class ClaimDraft(ModelOutput):
    text: str = Field(min_length=1, max_length=500)
    evidence_refs: tuple[str, ...] = ()


class ResearchClaims(ModelOutput):
    claims: tuple[ClaimDraft, ...] = Field(max_length=20)


class ResearchAgent:
    agent = AgentName.RESEARCH

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(self, task: ResearchTask) -> AgentOutcome[DossierDraft]:
        async def body(toolbox: Toolbox, session: ModelSession) -> BodyResult[DossierDraft]:
            return await self._body(task, toolbox, session)

        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="research",
            campaign_id=task.campaign_id,
            input_refs={
                "prospect_id": str(task.prospect_id),
                "domain": task.domain,
                "urls": list(task.urls),
            },
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    async def _body(
        self, task: ResearchTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[DossierDraft]:
        warnings: list[str] = []
        extractions: list[ResearchExtraction] = []
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
            extraction = extract_research(data.page)
            if extraction.injection_suspected:
                warnings.append(f"injection_suspected:{extraction.source_url}")
            if not extraction.evidence and not extraction.title:
                warnings.append(f"no_usable_content:{extraction.source_url}")
                continue
            extractions.append(extraction)
        if not extractions:
            if last_failure is not None:
                raise last_failure
            raise AgentFailure("no_usable_pages", "no fetched page produced evidence")

        evidence: dict[str, EvidenceExcerpt] = {}
        for extraction in extractions:
            for item in extraction.evidence:
                evidence.setdefault(item.content_hash, item)
        ordered = list(evidence.values())[:MAX_EVIDENCE]

        memory_text = ""
        try:
            memory = await call_typed(
                toolbox, "read_memory", ReadMemoryArgs(query=task.domain, limit=3), MemoryData
            )
            memory_text = "\n".join(
                wrap_untrusted(f"M{i + 1}", s.source_ref or "memory", s.text)
                for i, s in enumerate(memory.snippets)
            )
        except ToolCallFailed as exc:
            warnings.append(f"memory_unavailable:{exc}")

        ack = await call_typed(
            toolbox,
            "record_evidence",
            RecordEvidenceArgs(
                prospect_id=task.prospect_id,
                items=tuple(
                    EvidenceInput(
                        source_url=e.source_url,
                        excerpt=e.excerpt,
                        content_hash=e.content_hash,
                        kind=e.kind,
                    )
                    for e in ordered
                ),
            ),
            RecordEvidenceAck,
        )
        ids_by_hash = {r.content_hash: r.evidence_id for r in ack.refs}

        labels = {f"E{i + 1}": e for i, e in enumerate(ordered)}
        blocks = "\n".join(
            wrap_untrusted(label, e.source_url, f"[{e.kind}] {e.excerpt}")
            for label, e in labels.items()
        )
        prompt = (
            f"Site: {task.domain}\nEvidence blocks follow. Cite labels such as E1.\n\n{blocks}"
            + (f"\n\nPrior memory (also untrusted):\n{memory_text}" if memory_text else "")
        )
        drafted = await session.run_structured(ResearchClaims, system=SYSTEM_PROMPT, prompt=prompt)

        supported: list[str] = []
        unsupported: list[str] = []
        cited: dict[UUID, None] = {}
        for claim in drafted.claims:
            text = " ".join(claim.text.split())
            if looks_like_instruction(text):
                warnings.append("instruction_like_claim_dropped")
                continue
            refs = [labels[r].content_hash for r in claim.evidence_refs if r in labels]
            persisted = [ids_by_hash[h] for h in refs if h in ids_by_hash]
            if persisted:
                supported.append(text)
                for evidence_id in persisted:
                    cited.setdefault(evidence_id, None)
            else:
                unsupported.append(text)

        topics = tuple(dict.fromkeys(t for x in extractions for t in x.topics))
        if supported:
            summary = " ".join(supported)
            evidence_ids = tuple(cited)
        else:  # deterministic fallback: only what the extraction itself observed
            titles = [x.title for x in extractions if x.title]
            summary = "Pages reviewed: " + "; ".join(titles) if titles else ""
            evidence_ids = tuple(
                ids_by_hash[e.content_hash]
                for e in ordered
                if e.kind in SUMMARY_KINDS and e.content_hash in ids_by_hash
            )
        dossier = DossierDraft(
            prospect_id=task.prospect_id,
            summary=summary,
            topics=topics,
            evidence_ids=evidence_ids,
            unsupported_claims=tuple(unsupported),
        )
        await call_ack(toolbox, "record_dossier", RecordDossierArgs(dossier=dossier))
        total = len(supported) + len(unsupported)
        confidence = len(supported) / total if total else None
        return BodyResult(output=dossier, confidence=confidence, warnings=tuple(warnings))
