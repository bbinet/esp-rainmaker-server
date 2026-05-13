"""Test fixtures shared across the suite.

Two tiers:

* **Unit tests** (``tests/unit/``) — pure Python, no external services.
  Use the ``app_factory`` / ``client`` fixtures only.
* **Integration tests** (``tests/integration/``) — need Postgres (and
  later VerneMQ, Garage). Use ``pg_dsn`` / ``migrated_db`` / ``db`` /
  ``app_with_db`` / ``client_with_db`` which spawn an ephemeral
  Postgres+Timescale container via testcontainers, run Alembic
  migrations once per session, then override the ``get_db`` FastAPI
  dependency to point at the test database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

os.environ.setdefault("RM_ENV", "test")
os.environ.setdefault("RM_DEBUG", "true")
os.environ.setdefault("RM_SECRET_KEY", "test-secret-key-32-bytes-minimum-pad!")


@pytest.fixture
def app_factory() -> Any:
    from app.main import create_app

    return create_app


@pytest.fixture
def app(app_factory: Any) -> FastAPI:
    return app_factory()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    if not _docker_available():
        pytest.skip("docker not available")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        image="timescale/timescaledb:latest-pg16",
        dbname="rainmaker_test",
        username="rainmaker",
        password="rainmaker",
        driver=None,
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        async_dsn = f"postgresql+asyncpg://rainmaker:rainmaker@{host}:{port}/rainmaker_test"
        yield async_dsn
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_db(pg_dsn: str) -> str:
    # Use psycopg (v3) for the synchronous migration driver; the app uses asyncpg.
    sync_dsn = pg_dsn.replace("+asyncpg", "+psycopg")

    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_dsn)
    command.upgrade(cfg, "head")
    return pg_dsn


@pytest_asyncio.fixture
async def _truncate_tables(migrated_db: str) -> AsyncIterator[None]:
    """Truncate all phase-0 tables between tests."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(migrated_db, pool_pre_ping=True)
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE node_group_nodes, node_groups, "
                "node_sharing_requests, node_sharing, "
                "ota_job_nodes, ota_jobs, ota_images, "
                "claim_challenges, device_provisioning, "
                "mapping_challenges, user_node_mappings, "
                "node_attributes, node_params_shadow, node_configs, "
                "node_certificates, nodes, refresh_tokens, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def db(migrated_db: str, _truncate_tables: None) -> AsyncIterator[Any]:
    """Per-test AsyncSession bound to the test DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(migrated_db, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def app_with_db(
    migrated_db: str, app_factory: Any, _truncate_tables: None
) -> AsyncIterator[FastAPI]:
    """FastAPI app with ``get_db`` overridden to use the test DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.v1.deps.db import get_db

    engine = create_async_engine(migrated_db, pool_pre_ping=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _override_get_db() -> AsyncIterator[Any]:
        async with SessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app = app_factory()
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest_asyncio.fixture
async def client_with_db(app_with_db: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_with_db)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
