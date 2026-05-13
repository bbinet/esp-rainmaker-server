"""Alembic env: uses sync psycopg driver to run migrations.

The application talks asyncpg, but migrations stay synchronous for
simplicity. The DSN comes from whatever is set in ``sqlalchemy.url`` on
the alembic Config — either by ``alembic.ini`` (empty by default), by
the caller via ``Config.set_main_option(...)`` (used by the test suite)
or by application settings as a fallback (``RM_DATABASE_URL``).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401 — register tables
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Only fall back to the application settings if the caller did not
# already provide a URL via Config.set_main_option().
if not config.get_main_option("sqlalchemy.url"):
    from app.core.config import get_settings

    config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
