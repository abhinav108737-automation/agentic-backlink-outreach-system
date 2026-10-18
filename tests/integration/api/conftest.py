"""Fixtures for repository tests: a migrated template database cloned per test (fast, isolated)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from kb_contracts.config import Settings
from kb_core.db.engine import create_db_engine
from kb_core.db.uow import SqlUnitOfWorkFactory
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

ROOT = Path(__file__).resolve().parents[3]


def _url(db: str) -> object:
    return make_url(Settings(_env_file=None).sync_database_url).set(database=db)


def _dsn(db: str) -> str:
    return (
        _url(db)  # type: ignore[attr-defined]
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg", "postgresql")
    )


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    admin = _dsn("postgres")
    try:
        psycopg.connect(admin, connect_timeout=3).close()
    except Exception:  # noqa: BLE001
        if os.environ.get("KB_REQUIRE_DB") == "1" or os.environ.get("CI"):
            pytest.fail("PostgreSQL is required (KB_REQUIRE_DB=1/CI) but unreachable")
        pytest.skip("dev PostgreSQL not reachable")
    name = f"kb_tpl_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option(
        "sqlalchemy.url",
        _url(name).render_as_string(hide_password=False),  # type: ignore[attr-defined]
    )
    try:
        command.upgrade(cfg, "head")
        yield name
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def clone_db(template_db: str) -> Iterator[str]:
    name = f"kb_t_{uuid.uuid4().hex[:10]}"
    admin = _dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    try:
        yield name
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def engine(clone_db: str) -> AsyncIterator[AsyncEngine]:
    url = (
        make_url(Settings(_env_file=None).database_url)
        .set(database=clone_db)
        .render_as_string(hide_password=False)
    )
    eng = create_db_engine(url=url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


# ---- API-specific fixtures ------------------------------------------------------------

import httpx  # noqa: E402
from kb_api.app import create_app  # noqa: E402
from kb_core.db.seed import seed_policy_v1  # noqa: E402
from kb_core.scheduling.clock import FakeClock  # noqa: E402

TOKEN = "T0ken-" + "a1b2c3d4" * 6
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def seeded(factory: SqlUnitOfWorkFactory) -> SqlUnitOfWorkFactory:
    async with factory() as uow:
        await seed_policy_v1(uow)
    return factory


@pytest.fixture
async def client(seeded: SqlUnitOfWorkFactory) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(_env_file=None), TOKEN, probes=[], uow_factory=seeded, clock=FakeClock()
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1", headers=AUTH
    ) as c:
        yield c
