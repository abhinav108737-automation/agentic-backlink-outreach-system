"""Idempotency claim/replay for the `Idempotency-Key` header and internal operations.

The claim and the operation share ONE unit of work. Therefore:
  - if the operation fails, the claim rolls back too, so the caller can retry with the same key;
  - if two callers race, the loser blocks on the winner's unique-index entry, then sees the
    committed outcome and replays it instead of performing the work a second time.
The stored outcome is a `response_ref` (for example "campaign:<uuid>"); the caller resolves it
back to the resource, so no response bodies are persisted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kb_core.db.protocols import UnitOfWork
from kb_core.errors import IdempotencyInFlight, IdempotencyKeyReused, InvalidInput

MAX_KEY_CHARS = 200


def request_hash(payload: Any) -> str:
    """Stable fingerprint of a request body (key order and whitespace do not matter)."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class IdempotentOutcome:
    ref: str
    replayed: bool


class IdempotencyService:
    async def execute(
        self,
        uow: UnitOfWork,
        *,
        scope: str,
        key: str,
        payload: Any,
        operation: Callable[[], Awaitable[str]],
    ) -> IdempotentOutcome:
        if not key.strip() or len(key) > MAX_KEY_CHARS:
            raise InvalidInput(f"Idempotency-Key must be 1..{MAX_KEY_CHARS} characters")
        digest = request_hash(payload)
        if await uow.idempotency.claim(scope=scope, key=key, request_hash=digest):
            ref = await operation()
            await uow.idempotency.record_response(scope=scope, key=key, response_ref=ref)
            return IdempotentOutcome(ref, replayed=False)

        existing = await uow.idempotency.get(scope=scope, key=key)
        if existing is None:  # claim lost to a row that vanished: treat as not yet visible
            raise IdempotencyInFlight()
        if existing["request_hash"] != digest:
            raise IdempotencyKeyReused(f"Idempotency-Key {key!r} was used with a different request")
        recorded = existing.get("response_ref")
        if not recorded:
            raise IdempotencyInFlight()
        return IdempotentOutcome(str(recorded), replayed=True)
