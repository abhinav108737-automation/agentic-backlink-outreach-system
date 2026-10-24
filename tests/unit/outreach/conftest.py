"""PostgreSQL-backed fixtures for the Lane E flow tests: a migrated template database cloned per
test, plus a `World` that seeds one campaign / prospect / contact / sequence / evidence set
straight through the repositories (bypassing the services under test, so setup cannot hide a bug
in them). Offline: only PostgreSQL is needed."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from flowkit import T0, World, seed_prospect
from kb_contracts.config import Settings
from kb_contracts.enums import CampaignStatus
from kb_core.approvals.service import ApprovalService
from kb_core.db.engine import create_db_engine
from kb_core.db.seed import seed_policy_v1
from kb_core.db.uow import SqlUnitOfWork, SqlUnitOfWorkFactory
from kb_core.policy.engine import DeterministicPolicyEngine
from kb_core.scheduling.clock import FakeClock
from kb_core.sending.service import SendService as GateService
from kb_core.transitions.service import TransitionService
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

ROOT = Path(__file__).resolve().parents[3]


def _url(db: str) -> Any:
    return make_url(Settings(_env_file=None).sync_database_url).set(database=db)


def _dsn(db: str) -> str:
    return (
        _url(db).render_as_string(hide_password=False).replace("postgresql+psycopg", "postgresql")
    )


@pytest.fixture(scope="session")
def lane_e_template_db() -> Iterator[str]:
    admin = _dsn("postgres")
    try:
        psycopg.connect(admin, connect_timeout=3).close()
    except Exception:  # noqa: BLE001
        if os.environ.get("KB_REQUIRE_DB") == "1" or os.environ.get("CI"):
            pytest.fail("PostgreSQL is required (KB_REQUIRE_DB=1/CI) but unreachable")
        pytest.skip("dev PostgreSQL not reachable")
    name = f"kb_lanee_tpl_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _url(name).render_as_string(hide_password=False))
    try:
        command.upgrade(cfg, "head")
        yield name
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def lane_e_engine(lane_e_template_db: str) -> AsyncIterator[AsyncEngine]:
    name = f"kb_lanee_{uuid.uuid4().hex[:10]}"
    admin = _dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{lane_e_template_db}"')
    url = (
        make_url(Settings(_env_file=None).database_url)
        .set(database=name)
        .render_as_string(hide_password=False)
    )
    eng = create_db_engine(url=url)
    try:
        yield eng
    finally:
        await eng.dispose()
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def world(lane_e_engine: AsyncEngine) -> World:
    factory = SqlUnitOfWorkFactory(lane_e_engine)
    clock = FakeClock(T0)
    settings = Settings(_env_file=None)
    async with factory() as uow:
        assert isinstance(uow, SqlUnitOfWork)
        await seed_policy_v1(uow)
        campaign = await uow.campaigns.create(name="lane-e")
        campaign = await uow.campaigns.set_status(
            campaign.id, CampaignStatus.ACTIVE.value, expected_version=campaign.version
        )
        await uow.session.execute(
            sa.text(
                "INSERT INTO provider_accounts (provider_type, provider_name, display_name,"
                " config, is_paid, enabled, created_at, updated_at)"
                " VALUES ('email', 'mock_email', 'Mock sender', '{}', false, true, now(), now())"
            )
        )
        account_id: UUID = (
            await uow.session.execute(sa.text("SELECT id FROM provider_accounts"))
        ).scalar_one()
        await uow.session.execute(
            sa.text("UPDATE campaigns SET sender_provider_account_id = :a WHERE id = :c"),
            {"a": account_id, "c": campaign.id},
        )
        await uow.session.execute(
            sa.text(
                "INSERT INTO campaign_targets (campaign_id, target_url, keywords,"
                " anchor_preferences, priority, created_at, updated_at) VALUES"
                " (:c, 'https://kenresearch.example.test/reports', '{}', '{}', 1, now(), now())"
            ),
            {"c": campaign.id},
        )
    prospect, seq, evidence = await seed_prospect(
        factory, campaign, "editorial-desk.example.com", "editor@editorial-desk.example.com"
    )
    transitions = TransitionService(factory, clock)
    policy = DeterministicPolicyEngine(factory, settings, clock)
    gate = GateService(factory, policy, transitions, ApprovalService(factory), clock)
    return World(
        factory, clock, transitions, policy, gate, campaign, prospect, seq, evidence,
        "editor@editorial-desk.example.com", "editorial-desk.example.com",
    )  # fmt: skip
