"""Embedding providers: Ollama (default runtime, local, zero cost) and a deterministic hash
embedder for tests. Neither is a semantic-quality claim for the hash variant."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import httpx
from kb_contracts.providers import InvalidResponse, ProviderHealth, ProviderUnavailable


class HashEmbeddingProvider:
    """Deterministic bag-of-tokens hashing embedder. Test double only."""

    name = "hash-embed"
    is_paid = False

    def __init__(self, model: str = "hash-embed-v1", dimension: int = 32) -> None:
        self.model = model
        self._dim = dimension

    async def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, status="ok")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for tok in text.lower().split():
                h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
                vec[h % self._dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class OllamaEmbeddingProvider:
    name = "ollama-embed"
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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> ProviderHealth:
        try:
            r = await self._client.get(f"{self._base}/api/tags")
        except httpx.HTTPError as exc:
            return ProviderHealth(name=self.name, status="down", detail=type(exc).__name__)
        return ProviderHealth(name=self.name, status="ok" if r.status_code == 200 else "degraded")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            r = await self._client.post(
                f"{self._base}/api/embed", json={"model": self.model, "input": list(texts)}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        if r.status_code >= 500:
            raise ProviderUnavailable(f"ollama returned {r.status_code}")
        if r.status_code != 200:
            raise InvalidResponse(f"ollama rejected embed request: {r.status_code}")
        embeddings = r.json().get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise InvalidResponse("ollama returned an unexpected embedding payload")
        return [[float(x) for x in e] for e in embeddings]
