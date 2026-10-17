"""Shared helpers for the repository implementations (private to `kb_core.db`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kb_core.db.page import CursorPage, decode_cursor, encode_cursor


def utc(value: datetime, *, field: str = "timestamp") -> datetime:
    """Require an aware datetime and normalise it to UTC. Naive times are a bug, not a default."""
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware (UTC); got a naive datetime")
    return value.astimezone(UTC)


def ts_now() -> sa.ColumnElement[datetime]:
    """Wall-clock time at statement execution (not transaction start).

    `now()` is frozen for the whole transaction, which would make every row written inside one
    unit of work tie on `created_at` and make "latest" ordering and keyset pagination depend on
    random UUIDs. `clock_timestamp()` keeps insertion order observable. Always timestamptz/UTC.
    """
    return sa.func.clock_timestamp()


def stamp(**values: Any) -> dict[str, Any]:
    """Insert values plus explicit created_at/updated_at."""
    return {"created_at": ts_now(), "updated_at": ts_now(), **values}


def dto[D: BaseModel](cls: type[D], row: Mapping[Any, Any], **overrides: Any) -> D:
    """Build a frozen domain DTO from a row mapping, keeping only fields the DTO declares."""
    data = {k: row[k] for k in cls.model_fields if k in row}
    data.update(overrides)
    return cls.model_validate(data)


async def fetch_one(session: AsyncSession, stmt: sa.Executable) -> sa.RowMapping | None:
    result = await session.execute(stmt)
    row = result.mappings().first()
    return row


async def fetch_all(session: AsyncSession, stmt: sa.Executable) -> Sequence[sa.RowMapping]:
    result = await session.execute(stmt)
    return result.mappings().all()


async def keyset_page[T](
    session: AsyncSession,
    table: sa.Table,
    where: Sequence[sa.ColumnElement[bool]],
    *,
    cursor: str | None,
    limit: int,
    build: Callable[[sa.RowMapping], T],
) -> CursorPage[T]:
    """Keyset pagination over `(created_at, id)`; fetches limit+1 to know if there is a next."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    stmt = sa.select(table).where(*where)
    if cursor is not None:
        at, rid = decode_cursor(cursor)
        stmt = stmt.where(sa.tuple_(table.c.created_at, table.c.id) > sa.tuple_(at, rid))
    stmt = stmt.order_by(table.c.created_at, table.c.id).limit(limit + 1)
    rows = await fetch_all(session, stmt)
    page = rows[:limit]
    nxt: str | None = None
    if len(rows) > limit:
        last = page[-1]
        nxt = encode_cursor(last["created_at"], last["id"])
    return CursorPage([build(r) for r in page], nxt)


def as_uuid(value: object) -> UUID:
    assert isinstance(value, UUID)
    return value
