"""Integration tests for Phase 6 — OTA pipeline.

Covers the admin-side surface (register image, create job) and the
device-side flow (otafetch returns the pending job, otastatus updates
it). The Garage S3 backend is mocked through ``app.services.storage``
in tests; real signed-URL emission is exercised in compose e2e.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = pytest.mark.asyncio


SIGNUP_BODY = {"user_name": "ota-admin@example.com", "password": "Ota-Admin-1!"}


async def _make_admin_user(db, *, user_name: str = SIGNUP_BODY["user_name"]) -> None:
    from sqlalchemy import update

    from app.models.user import User

    await db.execute(update(User).where(User.user_name == user_name).values(is_admin=True))
    await db.commit()


async def _signup_login(client: httpx.AsyncClient, db, *, admin: bool = False) -> str:
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
    if admin:
        await _make_admin_user(db)
    login = (await client.post("/v1/login2", json=SIGNUP_BODY)).json()
    return login["accesstoken"]


async def _register_node_and_mapping(db, node_id: str, user_id: uuid.UUID) -> None:
    from app.models.node import Node, NodeCertificate
    from app.models.user_node import UserNodeMapping

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
    db.add(UserNodeMapping(user_id=user_id, node_id=node_id, role="primary", primary=True))
    await db.commit()


# ---------------- Admin: image upload ----------------


async def test_admin_image_upload_request_returns_signed_url(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db, admin=True)

    response = await client_with_db.post(
        "/v1/admin/otaimage/upload_request",
        headers={"Authorization": token},
        json={"name": "bulb-1.2.3", "fw_version": "1.2.3", "file_size": 1024 * 1024},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("upload_url")
    assert body.get("ota_image_id")
    assert body.get("storage_key")


async def test_admin_image_upload_confirm_marks_ready(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db, admin=True)

    init = (
        await client_with_db.post(
            "/v1/admin/otaimage/upload_request",
            headers={"Authorization": token},
            json={"name": "fw-1", "fw_version": "1.0.0", "file_size": 4096},
        )
    ).json()

    confirm = await client_with_db.post(
        "/v1/admin/otaimage/upload_confirm",
        headers={"Authorization": token},
        json={
            "ota_image_id": init["ota_image_id"],
            "file_md5": "5d41402abc4b2a76b9719d911017c592",
        },
    )
    assert confirm.status_code == 200

    from sqlalchemy import select

    from app.models.ota import OtaImage

    image = (
        await db.execute(select(OtaImage).where(OtaImage.id == uuid.UUID(init["ota_image_id"])))
    ).scalar_one()
    assert image.status == "ready"


async def test_admin_image_upload_request_rejects_non_admin(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db, admin=False)
    response = await client_with_db.post(
        "/v1/admin/otaimage/upload_request",
        headers={"Authorization": token},
        json={"name": "fw", "fw_version": "1.0.0", "file_size": 4096},
    )
    assert response.status_code == 403


# ---------------- Admin: OTA jobs ----------------


async def test_admin_create_otajob_targets_node(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db, admin=True)

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ota-node-1"
    await _register_node_and_mapping(db, node_id, user.id)

    init = (
        await client_with_db.post(
            "/v1/admin/otaimage/upload_request",
            headers={"Authorization": token},
            json={"name": "fw-job", "fw_version": "1.1.0", "file_size": 2048},
        )
    ).json()
    await client_with_db.post(
        "/v1/admin/otaimage/upload_confirm",
        headers={"Authorization": token},
        json={"ota_image_id": init["ota_image_id"], "file_md5": "abc"},
    )

    response = await client_with_db.post(
        "/v1/admin/otajob",
        headers={"Authorization": token},
        json={
            "name": "rollout-1",
            "ota_image_id": init["ota_image_id"],
            "nodes": [node_id],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("ota_job_id")

    from app.models.ota import OtaJobNode

    jn = (await db.execute(select(OtaJobNode).where(OtaJobNode.node_id == node_id))).scalar_one()
    assert jn.status == "pending"


# ---------------- Node: otafetch / otastatus ----------------


async def test_node_otafetch_returns_pending_job(client_with_db, db) -> None:
    admin_token = await _signup_login(client_with_db, db, admin=True)

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ota-fetch-1"
    await _register_node_and_mapping(db, node_id, user.id)

    init = (
        await client_with_db.post(
            "/v1/admin/otaimage/upload_request",
            headers={"Authorization": admin_token},
            json={"name": "fw-fetch", "fw_version": "1.5.0", "file_size": 8192},
        )
    ).json()
    await client_with_db.post(
        "/v1/admin/otaimage/upload_confirm",
        headers={"Authorization": admin_token},
        json={"ota_image_id": init["ota_image_id"], "file_md5": "deadbeef"},
    )
    await client_with_db.post(
        "/v1/admin/otajob",
        headers={"Authorization": admin_token},
        json={"name": "rollout-fetch", "ota_image_id": init["ota_image_id"], "nodes": [node_id]},
    )

    response = await client_with_db.get(
        "/v1/node/otafetch",
        headers={"X-SSL-Client-CN": node_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("ota_available") is True
    assert body.get("url")
    assert body.get("ota_job_id")
    assert body["fw_version"] == "1.5.0"


async def test_node_otastatus_updates_job(client_with_db, db) -> None:
    admin_token = await _signup_login(client_with_db, db, admin=True)

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ota-status-1"
    await _register_node_and_mapping(db, node_id, user.id)

    init = (
        await client_with_db.post(
            "/v1/admin/otaimage/upload_request",
            headers={"Authorization": admin_token},
            json={"name": "fw-st", "fw_version": "2.0.0", "file_size": 2048},
        )
    ).json()
    await client_with_db.post(
        "/v1/admin/otaimage/upload_confirm",
        headers={"Authorization": admin_token},
        json={"ota_image_id": init["ota_image_id"], "file_md5": "x"},
    )
    job = (
        await client_with_db.post(
            "/v1/admin/otajob",
            headers={"Authorization": admin_token},
            json={
                "name": "rollout-st",
                "ota_image_id": init["ota_image_id"],
                "nodes": [node_id],
            },
        )
    ).json()
    job_id = job["ota_job_id"]

    response = await client_with_db.post(
        "/v1/node/otastatus",
        headers={"X-SSL-Client-CN": node_id},
        json={"ota_job_id": job_id, "status": "success"},
    )
    assert response.status_code == 200

    from app.models.ota import OtaJobNode

    jn = (
        await db.execute(select(OtaJobNode).where(OtaJobNode.job_id == uuid.UUID(job_id)))
    ).scalar_one()
    assert jn.status == "success"


# ---------------- User: ota_status ----------------


async def test_user_ota_status_returns_per_node_status(client_with_db, db) -> None:
    token = await _signup_login(client_with_db, db, admin=True)

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    node_id = "ota-user-status"
    await _register_node_and_mapping(db, node_id, user.id)

    init = (
        await client_with_db.post(
            "/v1/admin/otaimage/upload_request",
            headers={"Authorization": token},
            json={"name": "fw-us", "fw_version": "3.0.0", "file_size": 1024},
        )
    ).json()
    await client_with_db.post(
        "/v1/admin/otaimage/upload_confirm",
        headers={"Authorization": token},
        json={"ota_image_id": init["ota_image_id"], "file_md5": "x"},
    )
    await client_with_db.post(
        "/v1/admin/otajob",
        headers={"Authorization": token},
        json={"name": "j-us", "ota_image_id": init["ota_image_id"], "nodes": [node_id]},
    )

    response = await client_with_db.get(
        "/v1/user/nodes/ota_status",
        headers={"Authorization": token},
        params={"node_id": node_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
