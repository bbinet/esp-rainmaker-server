"""Integration tests for Phase 5 — App-facing node APIs.

Mirrors the SDK calls in `esp-rainmaker-app-sdk-ts/src/methods/ESPRMUser`
and `ESPRMNode` modules:

  GET    /v1/user/nodes                  list nodes with optional details
  DELETE /v1/user/nodes/{node_id}        unmap (or hard-delete) a node
  GET    /v1/user/nodes/config           node config schema
  GET    /v1/user/nodes/status           connectivity
  GET    /v1/user/nodes/params           current shadow
  PUT    /v1/user/nodes/params           publish to params/remote
  PUT    /v1/user/nodes/mapping          legacy secret_key flow
  GET    /v1/user/nodes/mapping          poll mapping status
  POST   /v1/user/nodes/mapping/initiate challenge-response start
  POST   /v1/user/nodes/mapping/verify   challenge-response finish
  GET    /v1/user/custom_data            per-user JSON blob
  PUT    /v1/user/custom_data            update blob
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


SIGNUP_BODY = {"user_name": "node-user@example.com", "password": "Node-Pass-1!"}


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
    login = (await client.post("/v1/login2", json=SIGNUP_BODY)).json()
    return login["accesstoken"]


async def _claim_and_map(client: httpx.AsyncClient, db, token: str, node_id: str) -> None:
    """Create a Node + UserNodeMapping for the user via direct DB inserts."""
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from sqlalchemy import select

    from app.models.node import Node, NodeCertificate
    from app.models.user import User
    from app.models.user_node import UserNodeMapping

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()

    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    db.add(
        NodeCertificate(
            serial=f"ser-{node_id}",
            node_id=node_id,
            cn=node_id,
            cert_pem="",
            not_before=datetime.now(UTC),
            not_after=datetime.now(UTC) + timedelta(days=365),
        )
    )
    db.add(UserNodeMapping(user_id=user.id, node_id=node_id, role="primary", primary=True))
    await db.commit()
    _ = UUID  # keep import used
    _ = token


# ---------------- GET /v1/user/nodes ----------------


async def test_list_nodes_returns_user_owned_nodes(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    await _claim_and_map(client_with_db, db, token, "node-list-1")
    await _claim_and_map(client_with_db, db, token, "node-list-2")

    response = await client_with_db.get(
        "/v1/user/nodes",
        headers={"Authorization": token},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = sorted(body["nodes"])
    assert ids == ["node-list-1", "node-list-2"]


async def test_list_nodes_with_details(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    await _claim_and_map(client_with_db, db, token, "node-details-1")

    response = await client_with_db.get(
        "/v1/user/nodes",
        headers={"Authorization": token},
        params={"node_details": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "node_details" in body
    details = body["node_details"]
    assert isinstance(details, list)
    assert details[0]["id"] == "node-details-1"


async def test_list_nodes_excludes_other_users_nodes(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    # Insert another user's node without mapping it to our user.
    from datetime import UTC, datetime

    from app.models.node import Node

    db.add(Node(node_id="someone-else", registration_ts=datetime.now(UTC)))
    await db.commit()

    response = await client_with_db.get("/v1/user/nodes", headers={"Authorization": token})
    body = response.json()
    assert "someone-else" not in body["nodes"]


# ---------------- GET /v1/user/nodes/config ----------------


async def test_get_node_config_returns_payload(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    node_id = "node-cfg-app"
    await _claim_and_map(client_with_db, db, token, node_id)

    from app.models.node import NodeConfig

    db.add(NodeConfig(node_id=node_id, config_version="2020-01-01", payload={"devices": []}))
    await db.commit()

    response = await client_with_db.get(
        "/v1/user/nodes/config",
        headers={"Authorization": token},
        params={"node_id": node_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config_version"] == "2020-01-01"


async def test_get_node_config_forbidden_for_unrelated_user(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    from datetime import UTC, datetime

    from app.models.node import Node

    db.add(Node(node_id="not-mine", registration_ts=datetime.now(UTC)))
    await db.commit()

    response = await client_with_db.get(
        "/v1/user/nodes/config",
        headers={"Authorization": token},
        params={"node_id": "not-mine"},
    )
    assert response.status_code in {403, 404}


# ---------------- GET / PUT /v1/user/nodes/params ----------------


async def test_get_and_put_params(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    node_id = "node-params-app"
    await _claim_and_map(client_with_db, db, token, node_id)

    from app.models.node import NodeParamsShadow

    db.add(NodeParamsShadow(node_id=node_id, payload={"Light": {"power": False}}))
    await db.commit()

    # GET — initial state.
    response = await client_with_db.get(
        "/v1/user/nodes/params",
        headers={"Authorization": token},
        params={"node_id": node_id},
    )
    assert response.status_code == 200
    assert response.json()["Light"]["power"] is False

    # PUT — backend should accept and (in a real run) publish to MQTT.
    put = await client_with_db.put(
        "/v1/user/nodes/params",
        headers={"Authorization": token},
        params={"node_id": node_id},
        json={"Light": {"power": True}},
    )
    assert put.status_code == 200


# ---------------- /v1/user/nodes/mapping (legacy secret_key) ----------------


async def test_mapping_initiate_legacy_records_challenge(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    response = await client_with_db.put(
        "/v1/user/nodes/mapping",
        headers={"Authorization": token},
        json={
            "node_id": "node-mapping-legacy",
            "secret_key": "topsecret-XYZ",
            "operation": "add",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("request_id")

    from sqlalchemy import select

    from app.models.user_node import MappingChallenge

    row = (
        await db.execute(
            select(MappingChallenge).where(
                MappingChallenge.secret_key == "topsecret-XYZ",
            )
        )
    ).scalar_one()
    assert row.status == "pending"


async def test_mapping_status_polling(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    init = (
        await client_with_db.put(
            "/v1/user/nodes/mapping",
            headers={"Authorization": token},
            json={
                "node_id": "node-mapping-poll",
                "secret_key": "topsecret-POLL",
                "operation": "add",
            },
        )
    ).json()
    rid = init["request_id"]

    poll = await client_with_db.get(
        "/v1/user/nodes/mapping",
        headers={"Authorization": token},
        params={"request_id": rid},
    )
    assert poll.status_code == 200
    assert poll.json()["request_status"] == "pending"


# ---------------- /v1/user/nodes/mapping/{initiate,verify} ----------------


async def test_mapping_initiate_chal_resp_returns_challenge(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    response = await client_with_db.post(
        "/v1/user/nodes/mapping/initiate",
        headers={"Authorization": token},
        json={"node_id": "node-cr-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("challenge")
    assert body.get("request_id")


# ---------------- custom_data ----------------


async def test_custom_data_round_trip(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)

    put = await client_with_db.put(
        "/v1/user/custom_data",
        headers={"Authorization": token},
        json={"theme": "dark", "favorites": ["node-A", "node-B"]},
    )
    assert put.status_code == 200

    got = await client_with_db.get("/v1/user/custom_data", headers={"Authorization": token})
    assert got.status_code == 200
    body = got.json()
    assert body["theme"] == "dark"
    assert body["favorites"] == ["node-A", "node-B"]


# ---------------- DELETE /v1/user/nodes/{node_id} ----------------


async def test_delete_node_unmaps_for_user(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db)
    node_id = "node-delete-1"
    await _claim_and_map(client_with_db, db, token, node_id)

    response = await client_with_db.delete(
        f"/v1/user/nodes/{node_id}",
        headers={"Authorization": token},
    )
    assert response.status_code == 200

    listing = (await client_with_db.get("/v1/user/nodes", headers={"Authorization": token})).json()
    assert node_id not in listing["nodes"]
