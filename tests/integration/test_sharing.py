"""Integration tests for Phase 7 — sharing & groups."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _make_user(
    client: httpx.AsyncClient, db, user_name: str, password: str = "Pass-1!Strong"
) -> str:
    from sqlalchemy import select

    from app.models.user import User

    await client.post("/v1/user2", json={"user_name": user_name, "password": password})
    user = (await db.execute(select(User).where(User.user_name == user_name))).scalar_one()
    await client.put(
        "/v1/user2",
        json={"user_name": user_name, "verification_code": user.confirm_code},
    )
    return (
        await client.post("/v1/login2", json={"user_name": user_name, "password": password})
    ).json()["accesstoken"]


async def _claim_for(db, user_id: uuid.UUID, node_id: str) -> None:
    from app.models.node import Node
    from app.models.user_node import UserNodeMapping

    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    db.add(UserNodeMapping(user_id=user_id, node_id=node_id, role="primary", primary=True))
    await db.commit()


# ---------------- Sharing ----------------


async def test_share_node_creates_request_for_invitee(client_with_db, db) -> None:
    owner_token = await _make_user(client_with_db, db, "owner@example.com")
    from sqlalchemy import select

    from app.models.user import User

    owner = (
        await db.execute(select(User).where(User.user_name == "owner@example.com"))
    ).scalar_one()
    await _claim_for(db, owner.id, "shared-node-1")

    response = await client_with_db.put(
        "/v1/user/nodes/sharing/requests",
        headers={"Authorization": owner_token},
        json={"nodes": ["shared-node-1"], "user_name": "guest@example.com"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("request_id")


async def test_invitee_accepts_request_and_gets_access(client_with_db, db) -> None:
    owner_token = await _make_user(client_with_db, db, "owner2@example.com")
    from sqlalchemy import select

    from app.models.user import User

    owner = (
        await db.execute(select(User).where(User.user_name == "owner2@example.com"))
    ).scalar_one()
    await _claim_for(db, owner.id, "shared-node-2")

    init = (
        await client_with_db.put(
            "/v1/user/nodes/sharing/requests",
            headers={"Authorization": owner_token},
            json={"nodes": ["shared-node-2"], "user_name": "guest2@example.com"},
        )
    ).json()
    request_id = init["request_id"]

    # Sign up the invitee.
    guest_token = await _make_user(client_with_db, db, "guest2@example.com")

    accept = await client_with_db.put(
        "/v1/user/nodes/sharing/requests",
        headers={"Authorization": guest_token},
        json={"request_id": request_id, "accept": True},
    )
    assert accept.status_code == 200, accept.text

    # The guest now sees the node in their listing.
    listing = (
        await client_with_db.get("/v1/user/nodes", headers={"Authorization": guest_token})
    ).json()
    assert "shared-node-2" in listing["nodes"]


async def test_revoke_share_removes_access(client_with_db, db) -> None:
    owner_token = await _make_user(client_with_db, db, "owner3@example.com")
    from sqlalchemy import select

    from app.models.user import User

    owner = (
        await db.execute(select(User).where(User.user_name == "owner3@example.com"))
    ).scalar_one()
    node_id = "shared-node-3"
    await _claim_for(db, owner.id, node_id)

    init = (
        await client_with_db.put(
            "/v1/user/nodes/sharing/requests",
            headers={"Authorization": owner_token},
            json={"nodes": [node_id], "user_name": "guest3@example.com"},
        )
    ).json()

    guest_token = await _make_user(client_with_db, db, "guest3@example.com")
    await client_with_db.put(
        "/v1/user/nodes/sharing/requests",
        headers={"Authorization": guest_token},
        json={"request_id": init["request_id"], "accept": True},
    )

    # Revoke.
    guest = (
        await db.execute(select(User).where(User.user_name == "guest3@example.com"))
    ).scalar_one()
    revoke = await client_with_db.delete(
        "/v1/user/nodes/sharing",
        headers={"Authorization": owner_token},
        params={"nodes": node_id, "user_name": "guest3@example.com"},
    )
    assert revoke.status_code == 200

    listing = (
        await client_with_db.get("/v1/user/nodes", headers={"Authorization": guest_token})
    ).json()
    assert node_id not in listing["nodes"]
    _ = guest


# ---------------- Groups ----------------


async def test_create_group_and_list(client_with_db, db) -> None:
    token = await _make_user(client_with_db, db, "groups@example.com")
    response = await client_with_db.post(
        "/v1/user/node_group",
        headers={"Authorization": token},
        json={"group_name": "Living Room", "nodes": []},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("group_id")

    listing = (
        await client_with_db.get("/v1/user/node_group", headers={"Authorization": token})
    ).json()
    names = [g["group_name"] for g in listing["groups"]]
    assert "Living Room" in names


async def test_group_with_parent_and_mutually_exclusive(client_with_db, db) -> None:
    token = await _make_user(client_with_db, db, "groups2@example.com")
    parent = (
        await client_with_db.post(
            "/v1/user/node_group",
            headers={"Authorization": token},
            json={"group_name": "Home", "mutually_exclusive": True},
        )
    ).json()

    child = await client_with_db.post(
        "/v1/user/node_group",
        headers={"Authorization": token},
        json={"group_name": "Kitchen", "parent_group_id": parent["group_id"]},
    )
    assert child.status_code == 200

    from sqlalchemy import select

    from app.models.sharing import NodeGroup

    rows = (await db.execute(select(NodeGroup).where(NodeGroup.name == "Kitchen"))).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].parent_id) == parent["group_id"]


async def test_add_nodes_to_group(client_with_db, db) -> None:
    token = await _make_user(client_with_db, db, "groups3@example.com")
    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == "groups3@example.com"))
    ).scalar_one()
    await _claim_for(db, user.id, "group-node-A")
    await _claim_for(db, user.id, "group-node-B")

    group = (
        await client_with_db.post(
            "/v1/user/node_group",
            headers={"Authorization": token},
            json={"group_name": "Bedroom", "nodes": ["group-node-A", "group-node-B"]},
        )
    ).json()

    detail = await client_with_db.get(
        "/v1/user/node_group",
        headers={"Authorization": token},
        params={"group_id": group["group_id"]},
    )
    body = detail.json()
    nodes = body["groups"][0]["nodes"]
    assert sorted(nodes) == ["group-node-A", "group-node-B"]


async def test_delete_group_removes_membership(client_with_db, db) -> None:
    token = await _make_user(client_with_db, db, "groups4@example.com")
    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == "groups4@example.com"))
    ).scalar_one()
    await _claim_for(db, user.id, "group-node-C")

    g = (
        await client_with_db.post(
            "/v1/user/node_group",
            headers={"Authorization": token},
            json={"group_name": "Temp", "nodes": ["group-node-C"]},
        )
    ).json()

    response = await client_with_db.delete(
        f"/v1/user/node_group/{g['group_id']}",
        headers={"Authorization": token},
    )
    assert response.status_code == 200

    listing = (
        await client_with_db.get("/v1/user/node_group", headers={"Authorization": token})
    ).json()
    assert g["group_id"] not in [grp["group_id"] for grp in listing["groups"]]
    _ = timedelta
