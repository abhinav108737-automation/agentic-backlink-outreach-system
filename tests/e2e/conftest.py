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

ROOT = Path(__file__).resolve().parents[2]  # tests/e2e/ -> repo root


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


# ---- I1 agent-slice fixtures ----------------------------------------------------------
# The real ToolGateway wiring: deterministic services + fixture-backed mock providers + a
# deterministic model. No network, no Ollama, no live email.

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from kb_api.app import create_app  # noqa: E402
from kb_api.deps import Services, build_services  # noqa: E402
from kb_core.db import tables as t  # noqa: E402
from kb_core.db.seed import seed_policy_v1  # noqa: E402
from kb_core.scheduling.clock import FakeClock  # noqa: E402
from kb_providers.crawler import MockCrawlerProvider  # noqa: E402
from kb_providers.model import FakeModelProvider  # noqa: E402
from kb_providers.search import MockSearchProvider  # noqa: E402
from kb_providers.seo_metrics import MockSEOMetricsProvider  # noqa: E402
from kb_runtime import AgentStack, CapabilityDeps, build_agent_stack  # noqa: E402

TOKEN = "T0ken-" + "a1b2c3d4" * 6
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIXTURES = ROOT / "fixtures"

# The corpus' four missing-metric shapes. The first three are "fetched but unavailable"
# (state 2); the fourth is absent from baseline.csv entirely (state 3, never fetched).
DEFER_DOMAINS = (
    "emergingmarketspulse.io",
    "newtradejournal.com",
    "quietdataweekly.net",
)
NEVER_FETCHED_DOMAIN = "unlisted-no-metrics.example"
ELIGIBLE_DOMAIN = "marketinsightsjournal.com"


@pytest.fixture
async def seeded(factory: SqlUnitOfWorkFactory) -> SqlUnitOfWorkFactory:
    async with factory() as uow:
        await seed_policy_v1(uow)
    return factory


@pytest.fixture
def services(seeded: SqlUnitOfWorkFactory) -> Services:
    """The same deterministic service graph the API builds, so the agent slice and the API
    readback are provably looking at one system rather than two parallel wirings."""
    return build_services(Settings(_env_file=None), seeded, FakeClock())


@pytest.fixture
def deps(seeded: SqlUnitOfWorkFactory, services: Services) -> CapabilityDeps:
    return CapabilityDeps(
        uow_factory=seeded,
        search=MockSearchProvider(FIXTURES),
        crawler=MockCrawlerProvider(FIXTURES),
        seo_metrics=MockSEOMetricsProvider(FIXTURES),
        transitions=services.transitions,
    )


@pytest.fixture
async def campaign(seeded: SqlUnitOfWorkFactory) -> uuid.UUID:
    """An ACTIVE campaign with one target, so discovery has topics and seed queries."""
    async with seeded() as uow:
        created = await uow.campaigns.create(name="I1 slice")
        await uow.campaigns.set_status(created.id, "ACTIVE", expected_version=created.version)
        await uow.session.execute(
            sa.insert(t.campaign_targets).values(
                campaign_id=created.id,
                target_url="https://kenresearch.example/report",
                topic="market research",
                keywords=["analytics guest post"],
                priority=10,
            )
        )
    return created.id


@pytest.fixture
def stack(deps: CapabilityDeps, campaign: uuid.UUID, services: Services) -> AgentStack:
    return build_agent_stack(
        deps=deps,
        model=FakeModelProvider(seed=11),
        campaign_id=campaign,
        policy=services.policy,
        model_name="fake-model",
    )


@pytest.fixture
async def api(seeded: SqlUnitOfWorkFactory) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(_env_file=None), TOKEN, probes=[], uow_factory=seeded, clock=FakeClock()
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1", headers=AUTH
    ) as c:
        yield c


@pytest.fixture
async def agent_run(seeded: SqlUnitOfWorkFactory, campaign: uuid.UUID) -> uuid.UUID:
    """A real `agent_runs` row. Tool calls FK onto it, so passing a random UUID would make a
    gateway call fail on the audit write instead of on the thing under test."""
    async with seeded() as uow:
        return await uow.agent_runs.start(
            agent="discovery",
            task_type="e2e-probe",
            campaign_id=campaign,
            input_refs={},
            model=None,
            provider=None,
            correlation_id="i1-probe",
        )


# ---- I2 (Wave 2B) ---------------------------------------------------------------------
# The worker-side composition root, bound to the fixture-backed mocks and a deterministic
# model. There is no code path from here to a real mail server.

from kb_core.scheduling.worker import JobWorker  # noqa: E402
from kb_runtime import OutreachStack, build_outreach_stack  # noqa: E402


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def outreach(seeded: SqlUnitOfWorkFactory, campaign: uuid.UUID, clock: FakeClock) -> OutreachStack:
    return build_outreach_stack(
        settings=Settings(_env_file=None),
        uow_factory=seeded,
        clock=clock,
        fixture_root=FIXTURES,
        model=FakeModelProvider(seed=23),
        campaign_id=campaign,
    )


@pytest.fixture
def worker(seeded: SqlUnitOfWorkFactory, outreach: OutreachStack, clock: FakeClock) -> JobWorker:
    """The real bounded worker over the real registry — not a hand-rolled dispatcher, so the
    tests exercise claim/lease/retry exactly as production would."""
    return JobWorker(seeded, clock, outreach.registry, lease_owner="e2e", batch_size=20)
