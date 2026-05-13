"""Integration tests for Phase 4 — MQTT ingestor (topic router).

The ingestor subscribes to `node/+/+` on VerneMQ as a privileged backend
client and persists each message based on its topic suffix. We test the
*router* directly (pure function) by feeding it a fake MQTT message and
asserting the database side-effects. The real broker connection is
exercised in the e2e phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.asyncio


@dataclass
class FakeMqttMessage:
    topic: str
    payload: bytes

    @classmethod
    def from_dict(cls, topic: str, data: dict | list) -> FakeMqttMessage:
        return cls(topic=topic, payload=json.dumps(data).encode())


# ---------------- node/<id>/config ----------------


async def test_router_persists_node_config(db) -> None:
    from app.mqtt.router import dispatch

    node_id = "node-mqtt-cfg-1"
    cfg = {
        "node_id": node_id,
        "config_version": "2019-09-11",
        "info": {"name": "Bulb", "fw_version": "1.0.0"},
        "devices": [],
    }
    await dispatch(db, FakeMqttMessage.from_dict(f"node/{node_id}/config", cfg))

    from sqlalchemy import select

    from app.models.node import NodeConfig

    row = (await db.execute(select(NodeConfig).where(NodeConfig.node_id == node_id))).scalar_one()
    assert row.config_version == "2019-09-11"
    assert row.payload["info"]["name"] == "Bulb"


# ---------------- node/<id>/params/local ----------------


async def test_router_updates_params_shadow(db) -> None:
    from app.mqtt.router import dispatch

    node_id = "node-mqtt-params-1"
    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            f"node/{node_id}/params/local",
            {"Light": {"power": True, "brightness": 65}},
        ),
    )
    # Subsequent partial update.
    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            f"node/{node_id}/params/local",
            {"Light": {"brightness": 80}},
        ),
    )

    from sqlalchemy import select

    from app.models.node import NodeParamsShadow

    shadow = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one()
    assert shadow.payload["Light"]["power"] is True
    assert shadow.payload["Light"]["brightness"] == 80


async def test_router_handles_init_params(db) -> None:
    from app.mqtt.router import dispatch

    node_id = "node-mqtt-init"
    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            f"node/{node_id}/params/local/init",
            {"Light": {"power": False}},
        ),
    )

    from sqlalchemy import select

    from app.models.node import NodeParamsShadow

    shadow = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one()
    assert shadow.payload["Light"]["power"] is False


# ---------------- node/<id>/user/mapping ----------------


async def test_router_matches_user_mapping_challenge(db) -> None:
    """When the device publishes the secret_key, an existing pending
    mapping_challenge must be matched and a user_node_mapping created."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.node import Node
    from app.models.user import User
    from app.models.user_node import MappingChallenge, UserNodeMapping
    from app.mqtt.router import dispatch

    user = User(
        user_name="claimer-mqtt@example.com",
        email="claimer-mqtt@example.com",
        password_hash="x",
        status="confirmed",
    )
    db.add(user)
    node_id = "node-mqtt-mapping-1"
    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    await db.commit()
    await db.refresh(user)

    db.add(
        MappingChallenge(
            request_id="req-1",
            user_id=user.id,
            node_id=node_id,
            secret_key="topsecret-1234",
            status="pending",
            operation="add",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db.commit()

    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            f"node/{node_id}/user/mapping",
            {
                "node_id": node_id,
                "user_id": str(user.id),
                "secret_key": "topsecret-1234",
                "reset": False,
            },
        ),
    )

    mapping = (
        await db.execute(
            select(UserNodeMapping).where(
                UserNodeMapping.user_id == user.id,
                UserNodeMapping.node_id == node_id,
            )
        )
    ).scalar_one()
    assert mapping.role == "primary"

    challenge = (
        await db.execute(select(MappingChallenge).where(MappingChallenge.request_id == "req-1"))
    ).scalar_one()
    assert challenge.status == "completed"


async def test_router_ignores_mapping_with_unknown_secret(db) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.node import Node
    from app.models.user_node import UserNodeMapping
    from app.mqtt.router import dispatch

    node_id = "node-mqtt-mapping-2"
    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    await db.commit()

    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            f"node/{node_id}/user/mapping",
            {
                "node_id": node_id,
                "user_id": "00000000-0000-0000-0000-000000000000",
                "secret_key": "no-such-secret",
                "reset": False,
            },
        ),
    )

    mappings = (
        (await db.execute(select(UserNodeMapping).where(UserNodeMapping.node_id == node_id)))
        .scalars()
        .all()
    )
    assert mappings == []


# ---------------- node/<id>/otastatus ----------------
# (Phase 6 wires real OTA jobs; for now we just assert the router routes.)


async def test_router_ignores_unknown_otastatus_gracefully(db) -> None:
    from app.mqtt.router import dispatch

    # Should not raise even without a matching job.
    await dispatch(
        db,
        FakeMqttMessage.from_dict(
            "node/ghost/otastatus",
            {"ota_job_id": "missing", "status": "success"},
        ),
    )


# ---------------- Unknown topic ----------------


async def test_router_silently_drops_unknown_topic(db) -> None:
    from app.mqtt.router import dispatch

    await dispatch(db, FakeMqttMessage(topic="node/abc/garbage", payload=b"{}"))
    # No exception, no rows.
