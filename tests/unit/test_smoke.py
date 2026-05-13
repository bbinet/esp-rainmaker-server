from __future__ import annotations

import httpx
import pytest

from app import __version__


@pytest.mark.asyncio
async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


@pytest.mark.asyncio
async def test_apiversions_lists_v1(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/apiversions")
    assert response.status_code == 200
    body = response.json()
    assert "v1" in body["supported_versions"]


@pytest.mark.asyncio
async def test_mqtt_host_returns_broker(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/mqtt_host")
    assert response.status_code == 200
    body = response.json()
    assert "mqtt_host" in body
    assert ":" in body["mqtt_host"]
