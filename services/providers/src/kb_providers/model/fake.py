"""``FakeModelProvider``: deterministic, offline, seedable ``ModelProvider`` (the CI provider).

Given ``ModelRequest.output_schema`` it returns schema-valid structured output derived from a
seeded RNG, so the same seed and request always yield the same response. Failure routes
(invalid JSON, schema-violating output, timeout, ``ProviderUnavailable``, rate limiting) can be
queued so every agent failure path is testable without a model or a network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections import deque
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from kb_contracts.providers import (
    InvalidResponse,
    ModelRequest,
    ModelResponse,
    ProviderHealth,
    ProviderUnavailable,
    RateLimited,
)

from kb_providers.model.schema_gen import generate, violate

Scripted = Mapping[str, Any] | str | Callable[[ModelRequest], Mapping[str, Any] | str]


class FakeFailure(StrEnum):
    NONE = "none"
    INVALID_JSON = "invalid_json"  # text that is not JSON, structured=None
    SCHEMA_VIOLATION = "schema_violation"  # valid JSON that breaks the requested schema
    TIMEOUT = "timeout"  # raises ProviderUnavailable("timeout")
    UNAVAILABLE = "unavailable"  # raises ProviderUnavailable
    RATE_LIMITED = "rate_limited"  # raises RateLimited
    INVALID_RESPONSE = "invalid_response"  # raises InvalidResponse
    HANG = "hang"  # never returns; callers must enforce their own timeout


class FakeModelProvider:
    """Offline :class:`kb_contracts.providers.ModelProvider` for tests and CI.

    Args:
        seed: reproducibility seed mixed with each request.
        model: model name echoed in responses.
        failures: failure modes consumed one per ``generate`` call, then back to ``failure``.
        failure: the steady-state failure mode (default none).
        scripted: queue of exact responses (mapping -> structured, str -> raw text, or a
            callable of the request). Consumed before generic generation.
    """

    name = "fake-model"
    is_paid = False

    def __init__(
        self,
        *,
        seed: int = 0,
        model: str = "fake-model-v1",
        failure: FakeFailure = FakeFailure.NONE,
        failures: list[FakeFailure] | None = None,
        scripted: list[Scripted] | None = None,
    ) -> None:
        self.seed = seed
        self.model = model
        self.failure = failure
        self._failure_queue: deque[FakeFailure] = deque(failures or [])
        self._scripted: deque[Scripted] = deque(scripted or [])
        self.requests: list[ModelRequest] = []

    def fail_next(self, *modes: FakeFailure) -> None:
        self._failure_queue.extend(modes)

    def script(self, *responses: Scripted) -> None:
        self._scripted.extend(responses)

    async def health(self) -> ProviderHealth:
        down = self.failure in {FakeFailure.UNAVAILABLE, FakeFailure.TIMEOUT, FakeFailure.HANG}
        return ProviderHealth(
            name=self.name,
            status="down" if down else "ok",
            latency_ms=0,
            detail=f"simulated {self.failure.value}" if down else None,
        )

    def _rng(self, request: ModelRequest) -> random.Random:
        material = json.dumps(
            [self.seed, request.system, request.prompt, request.output_schema],
            sort_keys=True,
            default=str,
        )
        return random.Random(int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big"))

    def _count(self, text: str) -> int:
        return len(text.split())

    def _respond(
        self, request: ModelRequest, text: str, structured: dict[str, Any] | None
    ) -> ModelResponse:
        return ModelResponse(
            text=text,
            structured=structured,
            model=request.model or self.model,
            tokens_in=self._count(request.system) + self._count(request.prompt),
            tokens_out=self._count(text),
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        mode = self._failure_queue.popleft() if self._failure_queue else self.failure
        if mode is FakeFailure.UNAVAILABLE:
            raise ProviderUnavailable("fake provider unavailable")
        if mode is FakeFailure.TIMEOUT:
            raise ProviderUnavailable("timeout: fake provider did not answer in time")
        if mode is FakeFailure.RATE_LIMITED:
            raise RateLimited("fake provider rate limited")
        if mode is FakeFailure.INVALID_RESPONSE:
            raise InvalidResponse("fake provider returned an unusable payload")
        if mode is FakeFailure.HANG:
            await asyncio.sleep(3600)
        if mode is FakeFailure.INVALID_JSON:
            return self._respond(request, '{"unterminated": [1, 2,', None)

        if self._scripted and mode is not FakeFailure.SCHEMA_VIOLATION:
            item = self._scripted.popleft()
            if callable(item):
                item = item(request)
            if isinstance(item, str):
                return self._respond(request, item, None)
            data = dict(item)
            return self._respond(request, json.dumps(data, sort_keys=True), data)

        schema = request.output_schema
        if schema is None:
            return self._respond(request, "fake-model deterministic reply", None)
        instance = generate(schema, self._rng(request))
        if mode is FakeFailure.SCHEMA_VIOLATION:
            instance = violate(instance, schema, schema)
        structured = instance if isinstance(instance, dict) else {"value": instance}
        return self._respond(request, json.dumps(structured, sort_keys=True), structured)
