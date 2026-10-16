"""Async engine construction.

There is deliberately no module-level engine singleton: a hidden global would make the
database URL, pool size and lifetime invisible to callers and untestable (one of the
`.claude/rules/python-backend.md` rules is "no hidden provider globals"). The process owner
creates one engine, passes it to `create_uow_factory`, and disposes it on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from kb_contracts.config import Settings
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_db_engine(
    settings: Settings | None = None,
    *,
    url: str | None = None,
    pool_size: int = 5,
    max_overflow: int = 5,
    echo: bool = False,
) -> AsyncEngine:
    """Build an asyncpg engine from `Settings.database_url` (or an explicit override URL)."""
    dsn = url or (settings or Settings()).database_url
    if "+asyncpg" not in dsn:
        raise ValueError(f"database_url must use the asyncpg driver, got: {dsn.split('://')[0]}")
    return create_async_engine(
        dsn,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        echo=echo,
        # Every statement this layer issues is explicit SQLAlchemy Core; server-side prepared
        # statement caching is left on but named deterministically so pgbouncer-style poolers
        # (if ever introduced) fail loudly rather than silently reusing a stale plan.
        connect_args={"statement_cache_size": 0} if "asyncpg" in dsn else {},
    )


@asynccontextmanager
async def db_engine(
    settings: Settings | None = None, **kwargs: object
) -> AsyncIterator[AsyncEngine]:
    """Scoped engine for scripts and tests; disposes the pool on exit."""
    engine = create_db_engine(settings, **kwargs)  # type: ignore[arg-type]
    try:
        yield engine
    finally:
        await engine.dispose()
