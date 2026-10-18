"""Narrow agent-run persistence seam.

``kb_agents`` may not import ``kb_core.db``, so this protocol restates the write side of
``kb_core.db.protocols.AgentRunRepository`` (method names and keyword parameters are asserted
equal by ``tests/unit/agents/test_run_store_seam.py``). The Lead passes the real repository at I1.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class AgentRunStore(Protocol):
    async def start(
        self,
        *,
        agent: str,
        task_type: str,
        campaign_id: UUID | None,
        input_refs: dict[str, Any],
        model: str | None,
        provider: str | None,
        correlation_id: str | None,
        workflow_run_id: UUID | None = None,
        policy_version_id: UUID | None = None,
    ) -> UUID: ...

    async def finish(
        self,
        agent_run_id: UUID,
        *,
        structured_result: dict[str, Any] | None,
        confidence: float | None,
        latency_ms: int | None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        failure: str | None = None,
        fallback_used: bool = False,
    ) -> None: ...

    async def record_tool_call(
        self,
        *,
        agent_run_id: UUID,
        tool_name: str,
        args_redacted: dict[str, Any],
        result_summary: dict[str, Any] | None,
        status: str,
        policy_decision: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        correlation_id: str | None = None,
    ) -> UUID: ...
