"""Qdrant REST provider (httpx MockTransport) and in-memory provider behaviour."""

from __future__ import annotations

import json

import httpx
import pytest
from kb_contracts.providers import ProviderUnavailable, VectorPoint
from kb_providers.vector import InMemoryVectorProvider, InvalidPayload, QdrantVectorProvider

PAYLOAD = {"pg_id": "1", "embedding_model": "m", "model_version": "v", "content_hash": "h"}


def _point(i: str, vec: tuple[float, ...], payload: dict[str, str] | None = None) -> VectorPoint:
    return VectorPoint(id=i, vector=vec, payload=payload if payload is not None else PAYLOAD)


async def test_qdrant_creates_collection_then_upserts_with_payload() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body))
        if req.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"result": True})

    q = QdrantVectorProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await q.upsert("c", [_point("a", (1.0, 0.0))])
    assert seen[1] == ("PUT", "/collections/c", {"vectors": {"size": 2, "distance": "Cosine"}})
    method, path, body = seen[2]
    assert (method, path) == ("PUT", "/collections/c/points")
    assert body == {"points": [{"id": "a", "vector": [1.0, 0.0], "payload": PAYLOAD}]}


async def test_qdrant_rejects_payload_without_provenance() -> None:
    q = QdrantVectorProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
    )
    with pytest.raises(InvalidPayload, match="content_hash"):
        await q.upsert(
            "c", [_point("a", (1.0,), {"pg_id": "1", "embedding_model": "m", "model_version": "v"})]
        )


async def test_qdrant_down_degrades_instead_of_failing_hard() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    q = QdrantVectorProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    assert (await q.health()).status == "down"
    assert await q.search("c", [1.0], 3) == []  # recall degrades to empty
    with pytest.raises(ProviderUnavailable):  # writes raise a typed, catchable error
        await q.upsert("c", [_point("a", (1.0,))])


async def test_in_memory_search_ranks_by_cosine_and_delete() -> None:
    m = InMemoryVectorProvider()
    await m.upsert("c", [_point("near", (1.0, 0.1)), _point("far", (0.0, 1.0))])
    assert [p.id for p in await m.search("c", [1.0, 0.0], 2)] == ["near", "far"]
    await m.delete_collection("c")
    assert await m.search("c", [1.0, 0.0], 2) == []
    m.available = False
    assert (await m.health()).status == "down"
