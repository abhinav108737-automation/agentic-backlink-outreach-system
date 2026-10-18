"""Discovery agent -> ``DiscoveryResult`` (tools: search, read_campaign_state, record_candidates).

Candidates and their ``Provenance`` are built deterministically from what the search tool really
returned (provider name, the query actually issued, the result URL). The model only proposes
search queries when none were supplied, and never sees search-result text.
"""

from __future__ import annotations

from uuid import UUID

from kb_contracts.agents import CandidateDomain, DiscoveryResult, Provenance
from kb_contracts.enums import AgentName
from kb_core.discovery import InvalidUrl, canonicalize_url
from kb_core.research.untrusted import looks_like_instruction
from pydantic import Field

from kb_agents.agents.base import ModelOutput, TaskModel, call_ack, call_typed
from kb_agents.model_bridge import ModelSession
from kb_agents.prompts.common import UNTRUSTED_RULES
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
    CampaignStateView,
    ReadCampaignStateArgs,
    RecordCandidatesArgs,
    SearchArgs,
    SearchData,
)

PROMPT_VERSION = "discovery.prompt.v1"
SKILL_VERSION = "discovery.skill@0.1.0"
SYSTEM_PROMPT = (
    "You plan web searches that find sites accepting editorial backlinks for a research "
    "publisher. Propose short, distinct search queries about the campaign topics. "
    + UNTRUSTED_RULES
)


class DiscoveryTask(TaskModel):
    campaign_id: UUID
    queries: tuple[str, ...] = ()
    max_queries: int = Field(default=5, ge=1, le=10)
    limit_per_query: int = Field(default=10, ge=1, le=20)
    correlation_id: str | None = None


class QueryPlan(ModelOutput):
    queries: tuple[str, ...] = Field(min_length=1, max_length=10)


def _clean_queries(raw: tuple[str, ...], limit: int) -> list[str]:
    seen: dict[str, None] = {}
    for q in raw:
        text = " ".join(q.split())
        if text and len(text) <= 300 and not looks_like_instruction(text):
            seen.setdefault(text, None)
    return list(seen)[:limit]


class DiscoveryAgent:
    agent = AgentName.DISCOVERY

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(self, task: DiscoveryTask) -> AgentOutcome[DiscoveryResult]:
        async def body(toolbox: Toolbox, session: ModelSession) -> BodyResult[DiscoveryResult]:
            return await self._body(task, toolbox, session)

        return await execute_run(
            self._rt,
            agent=self.agent,
            task_type="discovery",
            campaign_id=task.campaign_id,
            input_refs={
                "campaign_id": str(task.campaign_id),
                "queries": list(task.queries),
                "max_queries": task.max_queries,
            },
            prompt_version=PROMPT_VERSION,
            skill_version=SKILL_VERSION,
            correlation_id=task.correlation_id,
            body=body,
        )

    async def _body(
        self, task: DiscoveryTask, toolbox: Toolbox, session: ModelSession
    ) -> BodyResult[DiscoveryResult]:
        state = await call_typed(
            toolbox,
            "read_campaign_state",
            ReadCampaignStateArgs(campaign_id=task.campaign_id),
            CampaignStateView,
        )
        queries = _clean_queries(task.queries or state.seed_queries, task.max_queries)
        if not queries:
            plan = await session.run_structured(
                QueryPlan,
                system=SYSTEM_PROMPT,
                prompt=(
                    f"Campaign: {state.name}\nTopics: {', '.join(state.topics) or 'none'}\n"
                    f"Propose up to {task.max_queries} search queries."
                ),
            )
            queries = _clean_queries(plan.queries, task.max_queries)
        if not queries:
            raise AgentFailure("no_queries", "no usable search queries")

        warnings: list[str] = []
        by_domain: dict[str, CandidateDomain] = {}
        urls_by_domain: dict[str, dict[str, None]] = {}
        last_failure: ToolCallFailed | None = None
        searched = 0
        for query in queries:
            try:
                data = await call_typed(
                    toolbox,
                    "search",
                    SearchArgs(query=query, limit=task.limit_per_query),
                    SearchData,
                )
            except ToolCallFailed as exc:
                last_failure = exc
                warnings.append(f"search_failed:{exc}")
                continue
            searched += 1
            for hit in data.results:
                try:
                    canon = canonicalize_url(hit.url)
                except InvalidUrl:
                    warnings.append("invalid_result_url")
                    continue
                domain = canon.registered_domain
                urls_by_domain.setdefault(domain, {}).setdefault(canon.canonical, None)
                if domain not in by_domain:
                    by_domain[domain] = CandidateDomain(
                        domain=domain,
                        provenance=Provenance(
                            source=data.provider, query=query, url=canon.canonical
                        ),
                    )
        if searched == 0 and last_failure is not None:
            raise last_failure

        candidates = tuple(
            c.model_copy(update={"urls": tuple(urls_by_domain[d])}) for d, c in by_domain.items()
        )
        result = DiscoveryResult(candidates=candidates)
        if candidates:
            await call_ack(
                toolbox,
                "record_candidates",
                RecordCandidatesArgs(campaign_id=task.campaign_id, result=result),
            )
        return BodyResult(output=result, confidence=None, warnings=tuple(warnings))
