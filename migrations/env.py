"""Alembic environment. URL from Settings (env), sync psycopg driver. Lane C owns this file."""

from alembic import context
from kb_contracts.config import Settings
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().sync_database_url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
