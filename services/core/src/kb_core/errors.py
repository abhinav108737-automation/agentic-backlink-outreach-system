"""Domain errors shared by the deterministic services (Lane B).

The API layer maps these to application/problem+json; services never raise HTTP types. Keeping
them in one small module means a route handler needs no knowledge of which service raised.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kb_contracts.policy import BlockCode, PolicyDecision


class EntityNotFound(LookupError):
    """The addressed row does not exist (maps to 404)."""

    def __init__(self, kind: str, ident: object) -> None:
        super().__init__(f"{kind} {ident} not found")
        self.kind, self.ident = kind, ident


class InvalidInput(ValueError):  # noqa: N818 - domain term
    """The request is well-formed but semantically unacceptable (maps to 422)."""


class DomainConflict(Exception):  # noqa: N818 - domain term
    """The request conflicts with current state, for example a decision made twice (409)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GuardFailed(DomainConflict):
    """A transition guard refused an edge that is structurally legal (409)."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code, message)
        self.detail = detail or {}


class PolicyBlocked(DomainConflict):
    """The deterministic policy gate blocked an action. Carries the full decision."""

    def __init__(self, decision: PolicyDecision) -> None:
        codes: Sequence[BlockCode] = decision.block_codes
        super().__init__("POLICY_BLOCKED", "blocked by policy: " + ", ".join(codes))
        self.decision = decision


class PolicyUnavailable(Exception):  # noqa: N818 - domain term
    """No effective policy version could be resolved. Fail closed: nothing proceeds (503)."""


class IdempotencyKeyReused(Exception):  # noqa: N818 - domain term
    """The same Idempotency-Key was presented with a different request body (422)."""


class IdempotencyInFlight(DomainConflict):
    """The key is claimed but has no recorded outcome yet (409, retry later)."""

    def __init__(self) -> None:
        super().__init__(
            "IDEMPOTENCY_IN_FLIGHT", "a request with this Idempotency-Key is in flight"
        )
