"""Agent-run lifecycle: every run yields an ``AgentRunRecord``, success or failure.

A provider error, schema-invalid model output, a refused/failed tool call or any unexpected error
becomes ``failure=<code>: <detail>`` with ``structured_result=None``. Nothing is coerced into a
passing result, and the record is persisted through the injected ``AgentRunStore``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from kb_contracts.agents import AgentRunRecord
from kb_contracts.enums import AgentName
from kb_contracts.providers import ProviderError, ProviderUnavailable, RateLimited
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from kb_agents.model_bridge import ModelRuntime, ModelSession
from kb_agents.runs.protocol import AgentRunStore
from kb_agents.tools.gateway import Toolbox, ToolGateway

log = logging.getLogger(__name__)


class AgentFailure(Exception):
    """A deterministic, expected failure inside an agent body (``code: detail`` is recorded)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class ToolCallFailed(AgentFailure):
    def __init__(self, tool: str, error_code: str | None) -> None:
        super().__init__(f"tool_failed:{tool}", error_code or "unknown")


@dataclass(frozen=True)
class AgentRuntime:
    """Everything an agent needs, all injected: no DB/email/crawler handle appears here."""

    gateway: ToolGateway
    runs: AgentRunStore
    models: ModelRuntime
    policy_version: str | None = None


@dataclass(frozen=True)
class BodyResult[T: BaseModel]:
    output: T
    confidence: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentOutcome[T: BaseModel]:
    record: AgentRunRecord
    output: T | None
    warnings: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.output is not None and self.record.failure is None


def _classify(exc: BaseException, session: ModelSession) -> str:
    if isinstance(exc, AgentFailure):
        return str(exc)
    if isinstance(exc, ProviderUnavailable):
        return f"provider_unavailable: {exc}"
    if isinstance(exc, RateLimited):
        return f"rate_limited: {exc}"
    if isinstance(exc, ProviderError):
        return f"provider_error:{type(exc).__name__}: {exc}"
    if isinstance(exc, TimeoutError):
        return "provider_timeout"
    if isinstance(exc, UnexpectedModelBehavior):
        return f"schema_invalid: {exc} [{session.last_raw_digest or 'no output'}]"
    if isinstance(exc, ValidationError):
        return f"schema_invalid: {exc.error_count()} validation errors in {exc.title}"
    return f"unexpected:{type(exc).__name__}"


async def execute_run[T: BaseModel](
    runtime: AgentRuntime,
    *,
    agent: AgentName,
    task_type: str,
    campaign_id: UUID | None,
    input_refs: dict[str, object],
    prompt_version: str,
    skill_version: str,
    correlation_id: str | None,
    body: Callable[[Toolbox, ModelSession], Awaitable[BodyResult[T]]],
) -> AgentOutcome[T]:
    session = runtime.models.new_session()
    started = time.perf_counter()
    try:
        run_id = await runtime.runs.start(
            agent=agent.value,
            task_type=task_type,
            campaign_id=campaign_id,
            input_refs=dict(input_refs),
            model=session.model_name,
            provider=session.provider_name,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # no audit trail => no tool calls at all
        log.exception("agent run could not be started")
        record = AgentRunRecord(
            agent_run_id=uuid4(),
            campaign_id=campaign_id,
            agent=agent,
            task_type=task_type,
            input_refs=dict(input_refs),
            prompt_version=prompt_version,
            skill_version=skill_version,
            policy_version=runtime.policy_version,
            failure=f"run_start_failed:{type(exc).__name__}",
        )
        return AgentOutcome(record=record, output=None)

    toolbox = runtime.gateway.bind(agent, run_id, correlation_id=correlation_id)
    output: T | None = None
    confidence: float | None = None
    warnings: tuple[str, ...] = ()
    failure: str | None = None
    try:
        result = await body(toolbox, session)
        output, confidence, warnings = result.output, result.confidence, result.warnings
    except asyncio.CancelledError:
        failure = "cancelled"
        await _finish(runtime, run_id, session, None, None, started, failure)
        raise
    except Exception as exc:
        failure = _classify(exc, session)
        if not isinstance(exc, (AgentFailure, ProviderError, UnexpectedModelBehavior)):
            log.exception("agent %s run failed", agent.value)

    structured = output.model_dump(mode="json") if output is not None else None
    finish_error = await _finish(runtime, run_id, session, structured, confidence, started, failure)
    if finish_error is not None:
        failure = f"{failure}; {finish_error}" if failure else finish_error
        output = None  # an unrecorded result is not a result
        structured = None
    latency_ms = int((time.perf_counter() - started) * 1000)
    record = AgentRunRecord(
        agent_run_id=run_id,
        campaign_id=campaign_id,
        agent=agent,
        task_type=task_type,
        input_refs=dict(input_refs),
        model=session.served_model or session.model_name,
        provider=session.provider_name,
        prompt_version=prompt_version,
        skill_version=skill_version,
        tool_calls=tuple(toolbox.calls),
        structured_result=structured,
        confidence=confidence if failure is None else None,
        policy_version=runtime.policy_version,
        latency_ms=latency_ms,
        failure=failure,
        fallback_used=session.fallback_used,
    )
    return AgentOutcome(record=record, output=output, warnings=warnings)


async def _finish(
    runtime: AgentRuntime,
    run_id: UUID,
    session: ModelSession,
    structured: dict[str, object] | None,
    confidence: float | None,
    started: float,
    failure: str | None,
) -> str | None:
    try:
        await runtime.runs.finish(
            run_id,
            structured_result=structured,
            confidence=confidence if failure is None else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens_in=session.tokens_in,
            tokens_out=session.tokens_out,
            failure=failure,
            fallback_used=session.fallback_used,
        )
    except Exception as exc:
        log.exception("agent run %s could not be finalised", run_id)
        return f"run_finish_failed:{type(exc).__name__}"
    return None
