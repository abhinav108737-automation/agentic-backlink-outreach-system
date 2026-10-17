"""Qdrant over its REST API (httpx). There is no qdrant client dependency.

Degradation contract: Qdrant being down must never take the application down, because
PostgreSQL holds the truth and the index is rebuildable.
- `health()` reports `down` instead of raising.
- `search()` returns `[]` (semantic recall degrades to nothing; deterministic paths are unaffected).
- `upsert()` / `delete_collection()` raise `ProviderUnavailable` (a typed, catchable error) so a
  caller can mark the index stale and the rebuild command can report "degraded".
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, Final

import httpx
from kb_contracts.providers import (
    InvalidResponse,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
    VectorPoint,
)

log = logging.getLogger(__name__)

REQUIRED_PAYLOAD_KEYS: Final = ("pg_id", "embedding_model", "model_version", "content_hash")


class InvalidPayload(ProviderError):
    """A point payload is missing the provenance keys that make the index rebuildable."""


def validate_payload(payload: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_PAYLOAD_KEYS if not payload.get(k)]
    if missing:
        raise InvalidPayload(f"payload missing required keys: {missing}")


class QdrantVectorProvider:
    name = "qdrant"
    is_paid = False  # local container; no per-call cost

    def __init__(
        self,
        base_url: str = "http://localhost:6333",
        *,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._ensured: set[tuple[str, int]] = set()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._client.request(method, f"{self._base}{path}", **kw)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"qdrant unreachable: {type(exc).__name__}") from exc

    @staticmethod
    def _check(resp: httpx.Response, *, allow_404: bool = False) -> None:
        if resp.status_code == 404 and allow_404:
            return
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"qdrant returned {resp.status_code}")
        if resp.status_code >= 400:
            raise InvalidResponse(f"qdrant rejected request: {resp.status_code} {resp.text[:200]}")

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            resp = await self._req("GET", "/collections")
            ok = resp.status_code == 200
        except ProviderUnavailable as exc:
            return ProviderHealth(name=self.name, status="down", detail=str(exc))
        return ProviderHealth(
            name=self.name,
            status="ok" if ok else "degraded",
            latency_ms=int((time.monotonic() - start) * 1000),
            detail=None if ok else f"status {resp.status_code}",
        )

    async def ensure_collection(self, collection: str, dimension: int) -> None:
        """Create the collection if absent (cosine). A dimension mismatch is a hard error:
        silently mixing embedding models in one collection corrupts search."""
        if (collection, dimension) in self._ensured:
            return
        resp = await self._req("GET", f"/collections/{collection}")
        if resp.status_code == 404:
            created = await self._req(
                "PUT",
                f"/collections/{collection}",
                json={"vectors": {"size": dimension, "distance": "Cosine"}},
            )
            self._check(created)
        else:
            self._check(resp)
            vectors = resp.json()["result"]["config"]["params"]["vectors"]
            existing = vectors.get("size") if isinstance(vectors, dict) else None
            if existing is not None and existing != dimension:
                raise InvalidPayload(
                    f"collection {collection} has dimension {existing}, got {dimension}; "
                    "rebuild the collection after an embedding-model change"
                )
        self._ensured.add((collection, dimension))

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        for p in points:
            validate_payload(p.payload)
        dim = len(points[0].vector)
        if any(len(p.vector) != dim for p in points):
            raise InvalidPayload("all vectors in one upsert must share a dimension")
        await self.ensure_collection(collection, dim)
        resp = await self._req(
            "PUT",
            f"/collections/{collection}/points",
            params={"wait": "true"},
            json={
                "points": [
                    {"id": p.id, "vector": list(p.vector), "payload": p.payload} for p in points
                ]
            },
        )
        self._check(resp)

    async def search(
        self, collection: str, vector: Sequence[float], limit: int
    ) -> list[VectorPoint]:
        try:
            resp = await self._req(
                "POST",
                f"/collections/{collection}/points/search",
                json={
                    "vector": list(vector),
                    "limit": limit,
                    "with_payload": True,
                    "with_vector": True,
                },
            )
            self._check(resp, allow_404=True)
        except (ProviderUnavailable, InvalidResponse) as exc:
            log.warning("vector search degraded to empty result: %s", exc)
            return []
        if resp.status_code == 404:
            return []
        hits = resp.json().get("result", [])
        return [
            VectorPoint(
                id=str(h["id"]), vector=tuple(h.get("vector") or ()), payload=h.get("payload") or {}
            )
            for h in hits
        ]

    async def delete_collection(self, collection: str) -> None:
        resp = await self._req("DELETE", f"/collections/{collection}")
        self._check(resp, allow_404=True)
        self._ensured = {e for e in self._ensured if e[0] != collection}
