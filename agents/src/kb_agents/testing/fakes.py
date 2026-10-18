"""Deterministic in-memory capabilities for tests and the offline MVP flow.

These are FAKES. The real repositories/crawler/search are wired behind the same ``Capability``
callables by the Lead at I1; nothing here touches a database, network or the filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from kb_contracts.providers import FetchedPage, ProviderUnavailable, SearchResult

from kb_agents.tools.gateway import Capability, ToolContext


class FakeWorld:
    """Backing state for the fake capabilities, plus a log of every mutating call."""

    def __init__(self) -> None:
        self.serp: dict[str, list[SearchResult]] = {}
        self.pages: dict[str, FetchedPage] = {}
        self.metrics: dict[str, dict[str, float | None]] = {}
        self.campaign: dict[str, Any] = {}
        self.policy: dict[str, Any] = {"policy_version": "policy-test-1"}
        self.memory: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.recorded: list[tuple[str, dict[str, Any]]] = []
        self.search_provider = "fake-search"
        self.fail_tools: dict[str, Exception] = {}

    def _maybe_fail(self, tool: str) -> None:
        if tool in self.fail_tools:
            raise self.fail_tools[tool]

    def capabilities(self) -> dict[str, Capability]:
        async def read_campaign_state(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("read_campaign_state")
            return {"campaign_id": args.campaign_id, **self.campaign}

        async def read_policy(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("read_policy")
            return self.policy

        async def read_memory(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("read_memory")
            return {"snippets": self.memory[: args.limit]}

        async def search(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("search")
            hits = self.serp.get(args.query, [])[: args.limit]
            return {"provider": self.search_provider, "results": hits}

        async def fetch_page(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("fetch_page")
            page = self.pages.get(args.url)
            if page is None:
                raise ProviderUnavailable(f"no fake page for {args.url}")
            return {"page": page}

        async def read_domain_metrics(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("read_domain_metrics")
            known = self.metrics.get(args.domain, {})
            # absent key = never fetched; None = fetched but unavailable; float = value
            return {
                "domain": args.domain,
                "metrics": {m: known[m] for m in args.metrics if m in known},
            }

        async def read_evidence(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("read_evidence")
            return {"items": self.evidence}

        async def record_evidence(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
            self._maybe_fail("record_evidence")
            refs = []
            for item in args.items:
                evidence_id = uuid4()
                refs.append({"content_hash": item.content_hash, "evidence_id": evidence_id})
                self.evidence.append({**item.model_dump(), "evidence_id": evidence_id})
            self.recorded.append(("record_evidence", args.model_dump(mode="json")))
            return {"refs": refs}

        def recorder(tool: str) -> Callable[[ToolContext, Any], Any]:
            async def record(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
                self._maybe_fail(tool)
                self.recorded.append((tool, args.model_dump(mode="json")))
                return {"record_id": uuid4()}

            return record

        caps: dict[str, Capability] = {
            "read_campaign_state": read_campaign_state,
            "read_policy": read_policy,
            "read_memory": read_memory,
            "search": search,
            "fetch_page": fetch_page,
            "read_domain_metrics": read_domain_metrics,
            "read_evidence": read_evidence,
            "record_evidence": record_evidence,
        }
        for tool in (
            "record_candidates",
            "record_assessment",
            "record_dossier",
            "record_contact_candidates",
            "record_draft",
            "record_classification",
            "record_negotiation_plan",
            "record_verification_assessment",
            "record_narrative",
            "request_send",
            "request_followup",
            "request_verification",
            "request_report",
            "propose_experiment",
            "escalate_to_human",
            "delegate_task",
        ):
            caps[tool] = recorder(tool)
        return caps


def new_campaign_id() -> UUID:
    return uuid4()
