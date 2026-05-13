"""Test fixtures shared across the suite.

Two tiers:

* **Unit tests** (`tests/unit/`) — pure Python, no external services.
  Use `app_factory` / `client` fixtures only.
* **Integration tests** (`tests/integration/`) — need Postgres, VerneMQ,
  Garage. Use the `pg_container` / `db` / `broker` / `garage` fixtures
  which spawn ephemeral containers via testcontainers and run Alembic
  migrations once per session.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
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


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _testcontainers_available() -> bool:
    try:
        import docker  # noqa: F401
        import testcontainers  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def pg_container() -> Iterator[Any]:
    """Ephemeral TimescaleDB-PG16 container for integration tests.

    Skipped if Docker / testcontainers aren't available locally — the
    integration suite will be marked as skipped in that case.
    """
    if not _testcontainers_available():
        pytest.skip("testcontainers / docker not available")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        image="timescale/timescaledb:latest-pg16",
        dbname="rainmaker_test",
        username="rainmaker",
        password="rainmaker",
    ) as pg:
        yield pg
