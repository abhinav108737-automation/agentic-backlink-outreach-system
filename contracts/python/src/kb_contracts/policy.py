"""C6: deterministic policy gate contract. No LLM may override a block."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import Field

from kb_contracts.domain import DTO


class BlockCode(StrEnum):
    SUPPRESSED = "SUPPRESSED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    HARD_THRESHOLD_FAILED = "HARD_THRESHOLD_FAILED"
    METRIC_MISSING = "METRIC_MISSING"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    DUPLICATE_CONTACT = "DUPLICATE_CONTACT"
    DUPLICATE_STEP = "DUPLICATE_STEP"
    SEND_LIMIT_EXCEEDED = "SEND_LIMIT_EXCEEDED"
    SENDER_INVALID = "SENDER_INVALID"
    UNSAFE_BROWSER_ACTION = "UNSAFE_BROWSER_ACTION"
    PROVIDER_AUTH_UNAVAILABLE = "PROVIDER_AUTH_UNAVAILABLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    REAL_SEND_DISABLED = "REAL_SEND_DISABLED"


class PolicyCheckRequest(DTO):
    action: str  # e.g. "qualify", "enqueue_send", "send", "follow_up"
    campaign_id: UUID
    subject_type: str
    subject_id: UUID
    context: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str


class PolicyDecision(DTO):
    allowed: bool
    block_codes: tuple[BlockCode, ...] = ()
    requires_approval: bool = False
    policy_version_id: UUID | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class PolicyEngine(Protocol):
    async def check(self, request: PolicyCheckRequest) -> PolicyDecision: ...
