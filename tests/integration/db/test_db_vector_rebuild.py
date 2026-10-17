"""Rebuild re-derives the vector index from PostgreSQL only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kb_core.db.uow import SqlUnitOfWorkFactory
from kb_core.db.vector_rebuild import rebuild_vectors
from kb_providers.vector import HashEmbeddingProvider, InMemoryVectorProvider
from sqlalchemy import text

pytestmark = pytest.mark.db


async def _seed(factory: SqlUnitOfWorkFactory) -> list[str]:
    async with factory() as uow:
        c = await uow.campaigns.create(name="c")
        d = await uow.domains.get_or_create("s.com")
        p = await uow.prospects.create(campaign_id=c.id, domain_id=d.id)
        ev = await uow.evidence.record_many(
            p.id,
            [
                {
                    "source_url": f"https://s.com/{i}",
                    "excerpt": f"text {i}",
                    "content_hash": f"h{i}",
                    "kind": "post",
                    "extracted_at": datetime(2026, 1, 1, tzinfo=UTC),
                }
                for i in range(3)
            ],
        )
        await uow.session.execute(
            text(
                "insert into memories (memory_type, subject_type, content, content_hash) "
                "values ('semantic', 'domain', 'prefers data posts', 'mh')"
            )
        )
    return [str(e.id) for e in ev]


async def test_rebuild_rederives_index_from_postgres(factory: SqlUnitOfWorkFactory) -> None:
    ids = await _seed(factory)
    vec, emb = InMemoryVectorProvider(), HashEmbeddingProvider()
    # stale junk that exists only in the vector store must disappear
    from kb_contracts.providers import VectorPoint

    await vec.upsert(
        "kb_evidence",
        [
            VectorPoint(
                id="stale",
                vector=tuple([0.0] * 32),
                payload={
                    "pg_id": "x",
                    "embedding_model": "m",
                    "model_version": "v",
                    "content_hash": "h",
                },
            )
        ],
    )
    report = await rebuild_vectors(factory, vec, emb, model_version="v1", batch_size=2)
    assert report.status == "ok" and report.indexed == {"kb_evidence": 3, "kb_memories": 1}
    pts = vec.collections["kb_evidence"]
    assert set(pts) == set(ids) and "stale" not in pts
    pl = pts[ids[0]].payload
    assert pl["pg_id"] == ids[0] and pl["embedding_model"] == "hash-embed-v1"
    assert pl["model_version"] == "v1" and pl["content_hash"].startswith("h")
    async with factory() as uow:
        n = (
            await uow.session.execute(text("select count(*) from embedding_index_records"))
        ).scalar_one()
    assert n == 4
    # idempotent: a second rebuild yields the same state, not duplicates
    again = await rebuild_vectors(factory, vec, emb, model_version="v2")
    assert again.status == "ok"
    async with factory() as uow:
        rows = (
            (
                await uow.session.execute(
                    text("select distinct model_version from embedding_index_records")
                )
            )
            .scalars()
            .all()
        )
        assert rows == ["v2"]


async def test_rebuild_degrades_when_vector_store_down(factory: SqlUnitOfWorkFactory) -> None:
    await _seed(factory)
    vec = InMemoryVectorProvider()
    vec.available = False
    report = await rebuild_vectors(factory, vec, HashEmbeddingProvider(), model_version="v")
    assert report.status == "degraded" and report.indexed == {}
    async with factory() as uow:
        n = (
            await uow.session.execute(text("select count(*) from embedding_index_records"))
        ).scalar_one()
    assert n == 0  # nothing claimed as indexed
