"""``OllamaEmbeddingProvider``: httpx adapter over local Ollama ``/api/embed`` (zero cost)."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Any

import httpx
from kb_contracts.providers import (
    InvalidResponse,
    ProviderHealth,
    ProviderUnavailable,
    RateLimited,
)


class OllamaEmbeddingProvider:
    name = "ollama-embedding"
    is_paid = False

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        *,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_settings(cls, settings: Any) -> OllamaEmbeddingProvider:
        return cls(model=settings.embedding_model, base_url=settings.ollama_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            r = await self._client.get(f"{self._base}/api/tags")
        except httpx.HTTPError as exc:
            return ProviderHealth(name=self.name, status="down", detail=type(exc).__name__)
        latency = int((time.perf_counter() - started) * 1000)
        return ProviderHealth(
            name=self.name,
            status="ok" if r.status_code == 200 else "degraded",
            latency_ms=latency,
            detail=None if r.status_code == 200 else f"http {r.status_code}",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            r = await self._client.post(
                f"{self._base}/api/embed", json={"model": self.model, "input": list(texts)}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise RateLimited("ollama rate limited")
        if r.status_code >= 500:
            raise ProviderUnavailable(f"ollama returned {r.status_code}")
        if r.status_code != 200:
            raise InvalidResponse(f"ollama rejected embed request: {r.status_code}")
        try:
            embeddings = r.json().get("embeddings")
        except (ValueError, AttributeError) as exc:
            raise InvalidResponse("ollama returned a non-JSON embedding payload") from exc
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise InvalidResponse("ollama returned an unexpected embedding payload")
        try:
            vectors = [[float(x) for x in e] for e in embeddings]
        except (TypeError, ValueError) as exc:
            raise InvalidResponse("ollama returned non-numeric embeddings") from exc
        if any(not math.isfinite(x) for v in vectors for x in v):
            raise InvalidResponse("ollama returned non-finite embedding values")
        return vectors
