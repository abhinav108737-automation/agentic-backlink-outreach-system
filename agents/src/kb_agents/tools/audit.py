"""Tool-call audit: redaction, the injected recorder protocol and a run-repository adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

_SECRET_KEY = re.compile(
    r"(secret|token|passw(or)?d|api[_-]?key|authorization|credential|cookie|bearer)", re.I
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
MAX_STRING_CHARS = 300
REDACTED = "[REDACTED]"


def redact(value: Any, *, _key: str = "") -> Any:
    """Return a copy safe to persist: secret-named keys masked, long text replaced by a digest."""
    if _key and _SECRET_KEY.search(_key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, _key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _BEARER.sub(REDACTED, value)
        if len(value) > MAX_STRING_CHARS:
            digest = hashlib.sha256(value.encode()).hexdigest()[:12]
            return f"[{len(value)} chars sha256:{digest}]"
        return value
    return value


def summarise_data(data: dict[str, Any]) -> dict[str, Any]:
    """Shape-only digest of a result (never raw page text or email bodies)."""
    out: dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(val, (list, tuple, dict)):
            out[key] = {"len": len(val)}
        elif isinstance(val, str):
            out[key] = {"chars": len(val)}
        else:
            out[key] = type(val).__name__
    return out


@dataclass(frozen=True)
class ToolCallAudit:
    agent: str
    agent_run_id: UUID | None
    tool: str
    args_redacted: dict[str, Any]
    result_summary: dict[str, Any]
    status: str  # ok | error | invalid_args | blocked | invalid_output
    policy_decision: dict[str, Any] | None = None
    latency_ms: int = 0
    correlation_id: str | None = None
    idempotency_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolCallRecorder(Protocol):
    async def record(self, audit: ToolCallAudit) -> None: ...


class MemoryToolCallRecorder:
    """In-memory recorder for tests."""

    def __init__(self) -> None:
        self.calls: list[ToolCallAudit] = []

    async def record(self, audit: ToolCallAudit) -> None:
        self.calls.append(audit)


class RunStoreRecorder:
    """Adapts an ``AgentRunStore`` (the ``AgentRunRepository`` seam) to ``ToolCallRecorder``."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def record(self, audit: ToolCallAudit) -> None:
        if audit.agent_run_id is None:
            return
        await self._store.record_tool_call(
            agent_run_id=audit.agent_run_id,
            tool_name=audit.tool,
            args_redacted=audit.args_redacted,
            result_summary=audit.result_summary,
            status=audit.status,
            policy_decision=audit.policy_decision,
            latency_ms=audit.latency_ms,
            correlation_id=audit.correlation_id,
        )
