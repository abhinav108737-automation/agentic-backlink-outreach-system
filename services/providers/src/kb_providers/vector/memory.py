"""In-memory `VectorProvider` for tests and offline runs. Same payload validation as Qdrant."""

from __future__ import annotations

import math
from collections.abc import Sequence

from kb_contracts.providers import ProviderHealth, ProviderUnavailable, VectorPoint

from kb_providers.vector.qdrant import InvalidPayload, validate_payload


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class InMemoryVectorProvider:
    name = "memory"
    is_paid = False

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, VectorPoint]] = {}
        self.available = True  # tests flip this to simulate an outage

    async def health(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, status="ok" if self.available else "down")

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        if not self.available:
            raise ProviderUnavailable("in-memory vector store marked unavailable")
        for p in points:
            validate_payload(p.payload)
        coll = self.collections.setdefault(collection, {})
        dims = {len(p.vector) for p in coll.values()} | {len(p.vector) for p in points}
        if len(dims) > 1:
            raise InvalidPayload("mixed vector dimensions in one collection")
        for p in points:
            coll[p.id] = p

    async def search(
        self, collection: str, vector: Sequence[float], limit: int
    ) -> list[VectorPoint]:
        if not self.available:
            return []
        pts = self.collections.get(collection, {}).values()
        return sorted(pts, key=lambda p: _cosine(p.vector, vector), reverse=True)[:limit]

    async def delete_collection(self, collection: str) -> None:
        if not self.available:
            raise ProviderUnavailable("in-memory vector store marked unavailable")
        self.collections.pop(collection, None)
