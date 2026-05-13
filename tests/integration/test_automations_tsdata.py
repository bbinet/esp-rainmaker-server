"""Integration tests for Phase 8 — automations & time-series."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = pytest.mark.asyncio


SIGNUP_BODY = {"user_name": "ts-user@example.com", "password": "Ts-Pass-1!"}


async def _signup_login(client: httpx.AsyncClient, db) -> str:
    from sqlalchemy import select

    from app.models.user import User

    await client.post("/v1/user2", json=SIGNUP_BODY)
    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    await client.put(
        "/v1/user2",
        json={"user_name": SIGNUP_BODY["user_name"], "verification_code": user.confirm_code},
    )
    return (await client.post("/v1/login2", json=SIGNUP_BODY)).json()["accesstoken"]


async def _claim_for(db, user_id: uuid.UUID, node_id: str) -> None:
    from app.models.node import Node
    from app.models.user_node import UserNodeMapping

    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    db.add(UserNodeMapping(user_id=user_id, node_id=node_id, role="primary", primary=True))
    await db.commit()


# ---------------- Automations CRUD ----------------


async def test_create_and_list_automation(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    payload = {
        "name": "Lights at sunset",
        "event_operator": "and",
        "events": [{"node_id": "n1", "device": "Light", "param": "power", "value": True}],
        "actions": [{"node_id": "n2", "device": "Fan", "param": "power", "value": True}],
    }
    response = await client_with_db.post(
        "/v1/user/node_automation",
        headers={"Authorization": token},
        json=payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("automation_id")

    listing = (
        await client_with_db.get("/v1/user/node_automation", headers={"Authorization": token})
    ).json()
    names = [a["name"] for a in listing["automations"]]
    assert "Lights at sunset" in names


async def test_update_automation_enabled_state(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    init = (
        await client_with_db.post(
            "/v1/user/node_automation",
            headers={"Authorization": token},
            json={"name": "A", "events": [], "actions": []},
        )
    ).json()

    update = await client_with_db.put(
        "/v1/user/node_automation",
        headers={"Authorization": token},
        json={"automation_id": init["automation_id"], "enabled": False},
    )
    assert update.status_code == 200

    listing = (
        await client_with_db.get("/v1/user/node_automation", headers={"Authorization": token})
    ).json()
    assert listing["automations"][0]["enabled"] is False


async def test_delete_automation(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    init = (
        await client_with_db.post(
            "/v1/user/node_automation",
            headers={"Authorization": token},
            json={"name": "B", "events": [], "actions": []},
        )
    ).json()

    response = await client_with_db.delete(
        "/v1/user/node_automation",
        headers={"Authorization": token},
        params={"automation_id": init["automation_id"]},
    )
    assert response.status_code == 200

    listing = (
        await client_with_db.get("/v1/user/node_automation", headers={"Authorization": token})
    ).json()
    assert listing["automations"] == []


# ---------------- tsdata ingest (via MQTT router) ----------------


async def test_router_ingests_tsdata_into_hypertable(db) -> None:
    from app.mqtt.router import dispatch

    class FakeMsg:
        def __init__(self, topic: str, payload: bytes) -> None:
            self.topic = topic
            self.payload = payload

    node_id = "ts-ingest-node"
    payload = {
        "ts_data_version": "2021-09-13",
        "ts_data": [
            {
                "name": "Light.brightness",
                "type": "esp.param.brightness",
                "dt": "int",
                "t": int(datetime.now(UTC).timestamp()),
                "records": [
                    {
                        "t": int(datetime.now(UTC).timestamp()) - 60,
                        "v": 50,
                    },
                    {
                        "t": int(datetime.now(UTC).timestamp()),
                        "v": 65,
                    },
                ],
            }
        ],
    }
    await dispatch(db, FakeMsg(f"node/{node_id}/tsdata", json.dumps(payload).encode()))

    from sqlalchemy import text

    count = (
        await db.execute(
            text("SELECT count(*) FROM tsdata WHERE node_id = :n"),
            {"n": node_id},
        )
    ).scalar_one()
    assert count == 2


async def test_router_ingests_simple_tsdata(db) -> None:
    from app.mqtt.router import dispatch

    class FakeMsg:
        def __init__(self, topic: str, payload: bytes) -> None:
            self.topic = topic
            self.payload = payload

    node_id = "simple-ts-node"
    payload = {
        "name": "Light.brightness",
        "type": "esp.param.brightness",
        "dt": "int",
        "t": int(datetime.now(UTC).timestamp()),
        "v": 42,
    }
    await dispatch(db, FakeMsg(f"node/{node_id}/simple_tsdata", json.dumps(payload).encode()))

    from sqlalchemy import text

    count = (
        await db.execute(
            text("SELECT count(*) FROM tsdata WHERE node_id = :n"),
            {"n": node_id},
        )
    ).scalar_one()
    assert count == 1


# ---------------- /v1/user/nodes/tsdata read with aggregation ----------------


async def test_user_tsdata_returns_points_in_range(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ts-read-node"
    await _claim_for(db, user.id, node_id)

    # Seed 5 points across 5 minutes.
    from sqlalchemy import text

    now = datetime.now(UTC)
    for i, value in enumerate([10, 20, 30, 40, 50]):
        await db.execute(
            text(
                "INSERT INTO tsdata (ts, node_id, device_name, param_name, data_type, value_int) "
                "VALUES (:ts, :nid, :d, :p, 'int', :v)"
            ),
            {
                "ts": now - timedelta(minutes=4 - i),
                "nid": node_id,
                "d": "Light",
                "p": "brightness",
                "v": value,
            },
        )
    await db.commit()

    response = await client_with_db.get(
        "/v1/user/nodes/tsdata",
        headers={"Authorization": token},
        params={
            "node_id": node_id,
            "param": "Light.brightness",
            "start_time": int((now - timedelta(minutes=10)).timestamp()),
            "end_time": int(now.timestamp()) + 60,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["records"]) == 5
    assert [r["v"] for r in body["records"]] == [10, 20, 30, 40, 50]


async def test_user_tsdata_aggregate_avg(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ts-agg-node"
    await _claim_for(db, user.id, node_id)

    from sqlalchemy import text

    now = datetime.now(UTC)
    for i, value in enumerate([10, 20, 30, 40, 50]):
        await db.execute(
            text(
                "INSERT INTO tsdata (ts, node_id, device_name, param_name, data_type, value_int) "
                "VALUES (:ts, :nid, :d, :p, 'int', :v)"
            ),
            {
                "ts": now - timedelta(minutes=4 - i),
                "nid": node_id,
                "d": "Light",
                "p": "brightness",
                "v": value,
            },
        )
    await db.commit()

    response = await client_with_db.get(
        "/v1/user/nodes/tsdata",
        headers={"Authorization": token},
        params={
            "node_id": node_id,
            "param": "Light.brightness",
            "start_time": int((now - timedelta(minutes=10)).timestamp()),
            "end_time": int(now.timestamp()) + 60,
            "aggregate": "avg",
            "aggregate_interval": "10m",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Average of [10..50] = 30 inside a single 10-minute bucket.
    assert len(body["records"]) >= 1
    assert abs(body["records"][0]["v"] - 30) < 0.001
