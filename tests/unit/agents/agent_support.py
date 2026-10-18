"""Shared builders for agent tests (fully offline)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from kb_agents.model_bridge import ModelRuntime
from kb_agents.runs import AgentRuntime, InMemoryAgentRunStore
from kb_agents.testing import FakeWorld
from kb_agents.tools import MemoryToolCallRecorder, ToolGateway
from kb_contracts.providers import FetchedPage, ModelProvider, SearchResult

HTML_ARTICLE = """<!doctype html><html><head><title>Healthcare Research Notes</title>
<meta name="description" content="Reviewed healthcare research analysis.">
<meta name="keywords" content="healthcare research,analysis"></head>
<body><h1>Healthcare Research Notes</h1>
<p>We publish reviewed healthcare research analysis. Editors check every submission.</p>
<p>Pitch <a href="mailto:editor@example-health.org">editor@example-health.org</a>.</p>
</body></html>"""


def page(url: str, html: str = HTML_ARTICLE, status: int = 200) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        status=status,
        html=html,
        text="",
        fetched_at=datetime(2026, 1, 5, tzinfo=UTC),
    )


@dataclass
class Rig:
    world: FakeWorld
    store: InMemoryAgentRunStore
    recorder: MemoryToolCallRecorder
    runtime: AgentRuntime
    campaign_id: UUID
    prospect_id: UUID


def make_rig(provider: ModelProvider, *, pydantic_model: Any = None, **gateway_kw: Any) -> Rig:
    world = FakeWorld()
    world.campaign = {"name": "Healthcare", "topics": ["healthcare"], "seed_queries": ["q one"]}
    world.policy = {
        "policy_version": "policy-test-1",
        "required_metrics": ["domain_authority", "spam_score"],
    }
    world.serp["q one"] = [
        SearchResult(url="https://www.example-health.org/write-for-us?utm_source=x", rank=1),
        SearchResult(url="https://example-health.org/blog/a", rank=2),
        SearchResult(url="https://other-site.net/", rank=3),
    ]
    world.pages["https://example-health.org/"] = page("https://example-health.org/")
    recorder = MemoryToolCallRecorder()
    store = InMemoryAgentRunStore()
    runtime = AgentRuntime(
        gateway=ToolGateway(capabilities=world.capabilities(), recorder=recorder, **gateway_kw),
        runs=store,
        models=ModelRuntime(provider, pydantic_model=pydantic_model, timeout_s=5),
        policy_version="policy-test-1",
    )
    return Rig(world, store, recorder, runtime, uuid4(), uuid4())
