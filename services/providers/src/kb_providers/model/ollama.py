"""``OllamaModelProvider``: httpx adapter over a local Ollama server (default runtime, ADR-0004).

Zero cost, ``is_paid = False``. Raises only the shared error taxonomy; an absent or unreachable
server is ``ProviderUnavailable`` and ``health()`` reports ``down`` instead of crashing.
No test requires a running Ollama: the client is injectable (``httpx.MockTransport``).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from kb_contracts.providers import (
    AuthRequired,
    InvalidResponse,
    ModelRequest,
    ModelResponse,
    ProviderHealth,
    ProviderUnavailable,
    RateLimited,
)


class OllamaModelProvider:
    name = "ollama"
    is_paid = False

    def __init__(
        self,
        model: str = "qwen3:8b-q4_K_M",
        base_url: str = "http://localhost:11434",
        *,
        timeout: float = 120.0,
        temperature: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._temperature = temperature
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_settings(cls, settings: Any) -> OllamaModelProvider:
        """Build from ``kb_contracts.config.Settings`` (``ollama_url``, ``reasoning_model``)."""
        return cls(model=settings.reasoning_model, base_url=settings.ollama_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            r = await self._client.get(f"{self._base}/api/tags")
        except httpx.HTTPError as exc:
            return ProviderHealth(name=self.name, status="down", detail=type(exc).__name__)
        latency = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return ProviderHealth(
                name=self.name,
                status="degraded",
                latency_ms=latency,
                detail=f"http {r.status_code}",
            )
        return ProviderHealth(name=self.name, status="ok", latency_ms=latency)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self.model
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "options": {"temperature": self._temperature},
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
        }
        if request.output_schema is not None:
            payload["format"] = request.output_schema
        try:
            r = await self._client.post(f"{self._base}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise RateLimited("ollama rate limited")
        if r.status_code in {401, 403}:
            raise AuthRequired(f"ollama refused the request: {r.status_code}")
        if r.status_code >= 500:
            raise ProviderUnavailable(f"ollama returned {r.status_code}")
        if r.status_code != 200:
            raise InvalidResponse(f"ollama rejected the request: {r.status_code}")
        try:
            body = r.json()
            text = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidResponse("ollama returned an unexpected chat payload") from exc
        if not isinstance(text, str):
            raise InvalidResponse("ollama message content is not text")
        structured: dict[str, Any] | None = None
        if request.output_schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None  # left unparsed: the typed-output validator rejects it downstream
            structured = parsed if isinstance(parsed, dict) else None
        return ModelResponse(
            text=text,
            structured=structured,
            model=str(body.get("model") or model),
            tokens_in=int(body.get("prompt_eval_count") or 0),
            tokens_out=int(body.get("eval_count") or 0),
        )
