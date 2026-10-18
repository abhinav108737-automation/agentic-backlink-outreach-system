"""In-memory ``AgentRunStore`` (test double; the real one is the Postgres repository)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4


class InMemoryAgentRunStore:
    def __init__(self) -> None:
        self.runs: dict[UUID, dict[str, Any]] = {}
        self.tool_calls: list[dict[str, Any]] = []
        self.fail_on_start = False
        self.fail_on_finish = False

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
    ) -> UUID:
        if self.fail_on_start:
            raise RuntimeError("store unavailable")
        run_id = uuid4()
        self.runs[run_id] = {
            "agent_run_id": run_id,
            "agent": agent,
            "task_type": task_type,
            "campaign_id": campaign_id,
            "input_refs": input_refs,
            "model": model,
            "provider": provider,
            "correlation_id": correlation_id,
            "finished": False,
        }
        return run_id

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
    ) -> None:
        if self.fail_on_finish:
            raise RuntimeError("store unavailable")
        self.runs[agent_run_id].update(
            finished=True,
            structured_result=structured_result,
            confidence=confidence,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            failure=failure,
            fallback_used=fallback_used,
        )

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
    ) -> UUID:
        call_id = uuid4()
        self.tool_calls.append(
            {
                "id": call_id,
                "agent_run_id": agent_run_id,
                "tool_name": tool_name,
                "args_redacted": args_redacted,
                "result_summary": result_summary,
                "status": status,
                "policy_decision": policy_decision,
                "latency_ms": latency_ms,
            }
        )
        return call_id
