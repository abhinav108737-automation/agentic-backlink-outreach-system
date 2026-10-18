"""kb_agents' AgentRunStore must stay a faithful subset of the DB-layer AgentRunRepository."""

import inspect

from kb_agents.runs import InMemoryAgentRunStore
from kb_agents.runs.protocol import AgentRunStore
from kb_core.db.protocols import AgentRunRepository


def _params(fn: object) -> list[tuple[str, object, object]]:
    sig = inspect.signature(fn)  # type: ignore[arg-type]
    return [(p.name, p.kind, p.default) for p in sig.parameters.values()]


def test_store_methods_match_repository_seam() -> None:
    for name in ("start", "finish", "record_tool_call"):
        assert _params(getattr(AgentRunStore, name)) == _params(
            getattr(AgentRunRepository, name)
        ), name


def test_in_memory_store_matches_protocol() -> None:
    for name in ("start", "finish", "record_tool_call"):
        assert _params(getattr(InMemoryAgentRunStore, name)) == _params(
            getattr(AgentRunStore, name)
        ), name
