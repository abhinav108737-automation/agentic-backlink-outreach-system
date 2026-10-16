import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from kb_contracts.config import Settings
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def _admin_dsn() -> str:
    u = make_url(Settings(_env_file=None).sync_database_url)
    return (
        u.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg", "postgresql")
    )


@pytest.fixture(scope="session")
def pg_available() -> bool:
    try:
        with psycopg.connect(_admin_dsn(), connect_timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def scratch_db(pg_available: bool) -> Iterator[tuple[str, str]]:
    """A throwaway database so migration tests never touch dev data. Yields (sqla_url, dsn)."""
    if not pg_available:
        if os.environ.get("KB_REQUIRE_DB") == "1" or os.environ.get("CI"):
            pytest.fail(
                "PostgreSQL is required (KB_REQUIRE_DB=1/CI) but unreachable"
            )  # no silent skip
        pytest.skip(
            "dev PostgreSQL not reachable (docker compose -f infrastructure/docker/compose.dev.yml up -d)"
        )
    name = f"kb_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_dsn(), autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    base = make_url(Settings(_env_file=None).sync_database_url).set(database=name)
    sqla = base.render_as_string(hide_password=False)
    dsn = sqla.replace("postgresql+psycopg", "postgresql")
    try:
        yield sqla, dsn
    finally:
        with psycopg.connect(_admin_dsn(), autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def alembic_cfg(scratch_db: tuple[str, str]) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", scratch_db[0])
    return cfg
