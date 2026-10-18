"""``FakeEmbeddingProvider``: deterministic hash embedder (test double, no semantic claim)."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from kb_contracts.providers import ProviderHealth, ProviderUnavailable


class FakeEmbeddingProvider:
    name = "fake-embedding"
    is_paid = False

    def __init__(
        self, model: str = "fake-embed-v1", dimension: int = 32, *, unavailable: bool = False
    ) -> None:
        self.model = model
        self._dim = dimension
        self._unavailable = unavailable

    async def health(self) -> ProviderHealth:
        if self._unavailable:
            return ProviderHealth(name=self.name, status="down", detail="simulated unavailable")
        return ProviderHealth(name=self.name, status="ok", latency_ms=0)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._unavailable:
            raise ProviderUnavailable("fake embedding provider unavailable")
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for tok in text.lower().split():
                h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
                vec[h % self._dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
