"""Rebuild the derived Qdrant index from PostgreSQL.

PostgreSQL is authoritative; Qdrant holds nothing that cannot be re-derived. This command
re-derives every vector from the source rows (`evidence_items.excerpt`, `memories.content`) by
re-embedding them, then rewrites `embedding_index_records` so PostgreSQL knows exactly what is
indexed under which embedding model/version/hash (docs/04 "Qdrant (derived)").

Safety properties:
- Qdrant down -> a `degraded` report and exit code 2, no exception, nothing half-recorded.
- `embedding_index_records` for a collection are only replaced in the same transaction that
  follows a fully successful re-index of that collection.
- It reads and writes only through the provider protocols; no Qdrant client is imported here.

Usage: `uv run python -m kb_core.db.vector_rebuild [--model-version V] [--batch-size N]`
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

import sqlalchemy as sa
from kb_contracts.providers import (
    EmbeddingProvider,
    ProviderError,
    ProviderUnavailable,
    VectorPoint,
    VectorProvider,
)

from kb_core.db import tables as t
from kb_core.db._common import fetch_all, stamp, ts_now
from kb_core.db.uow import SqlUnitOfWorkFactory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexSource:
    table: sa.Table
    collection: str
    text_column: str


SOURCES: Final = (
    IndexSource(t.evidence_items, "kb_evidence", "excerpt"),
    IndexSource(t.memories, "kb_memories", "content"),
)


@dataclass
class RebuildReport:
    status: str = "ok"  # "ok" | "degraded"
    indexed: dict[str, int] = field(default_factory=dict)
    detail: str | None = None


async def rebuild_vectors(
    factory: SqlUnitOfWorkFactory,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    *,
    model_version: str,
    batch_size: int = 64,
    sources: tuple[IndexSource, ...] = SOURCES,
) -> RebuildReport:
    report = RebuildReport()
    health = await vectors.health()
    if health.status != "ok":
        report.status, report.detail = "degraded", f"vector store {health.status}: {health.detail}"
        return report

    for src in sources:
        try:
            report.indexed[src.collection] = await _rebuild_collection(
                factory, vectors, embedder, src, model_version, batch_size
            )
        except ProviderUnavailable as exc:
            report.status, report.detail = "degraded", f"{src.collection}: {exc}"
            log.warning("vector rebuild degraded: %s", report.detail)
            return report
    return report


async def _rebuild_collection(
    factory: SqlUnitOfWorkFactory,
    vectors: VectorProvider,
    embedder: EmbeddingProvider,
    src: IndexSource,
    model_version: str,
    batch_size: int,
) -> int:
    tbl = src.table
    await vectors.delete_collection(src.collection)  # start from empty: stale points must go
    records: list[dict[str, object]] = []
    last_id: UUID | None = None
    async with factory() as uow:
        s = uow.session
        while True:
            stmt = sa.select(tbl.c.id, tbl.c[src.text_column], tbl.c.content_hash)
            if last_id is not None:
                stmt = stmt.where(tbl.c.id > last_id)
            rows = await fetch_all(s, stmt.order_by(tbl.c.id).limit(batch_size))
            if not rows:
                break
            last_id = rows[-1]["id"]
            vecs = await embedder.embed([r[src.text_column] for r in rows])
            points = [
                VectorPoint(
                    id=str(r["id"]),
                    vector=tuple(v),
                    payload={
                        "pg_id": str(r["id"]),
                        "source_table": tbl.name,
                        "embedding_model": embedder.model,
                        "model_version": model_version,
                        "content_hash": r["content_hash"],
                    },
                )
                for r, v in zip(rows, vecs, strict=True)
            ]
            await vectors.upsert(src.collection, points)
            records.extend(
                stamp(
                    source_table=tbl.name,
                    source_id=r["id"],
                    collection=src.collection,
                    embedding_model=embedder.model,
                    model_version=model_version,
                    content_hash=r["content_hash"],
                    indexed_at=ts_now(),
                )
                for r in rows
            )
        e = t.embedding_index_records
        await s.execute(
            sa.delete(e).where(e.c.collection == src.collection, e.c.source_table == tbl.name)
        )
        if records:
            await s.execute(sa.insert(e).values(records))
    return len(records)


async def _main(argv: list[str]) -> int:
    from kb_contracts.config import Settings

    from kb_core.db.engine import db_engine

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-version", default="unversioned", help="embedding model version tag")
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args(argv)

    from kb_providers.vector import OllamaEmbeddingProvider, QdrantVectorProvider

    settings = Settings()
    vectors = QdrantVectorProvider(settings.qdrant_url)
    embedder = OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_url)
    try:
        async with db_engine(settings) as engine:
            try:
                report = await rebuild_vectors(
                    SqlUnitOfWorkFactory(engine),
                    vectors,
                    embedder,
                    model_version=args.model_version,
                    batch_size=args.batch_size,
                )
            except ProviderError as exc:  # e.g. Ollama down: degrade, don't traceback
                print(f"rebuild degraded: {exc}")
                return 2
    finally:
        await vectors.aclose()
        await embedder.aclose()
    print(f"rebuild {report.status}: {report.indexed} {report.detail or ''}".strip())
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main(sys.argv[1:])))
