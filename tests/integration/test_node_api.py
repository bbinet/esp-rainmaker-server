"""Integration tests for Phase 3 — Node-facing HTTPS API (mTLS).

The firmware uses these routes over mTLS to publish its config, sync
params, poll for OTA updates and report OTA status. In the Kubernetes
deployment the `node.<domain>` Ingress is configured with
``auth-tls-verify-client: on`` and propagates the validated CN as the
``X-SSL-Client-CN`` request header. The backend trusts that header
because anything reaching it via that Ingress was already mTLS-checked.

Tests therefore drive the API directly with the header set, simulating
what the Ingress would do.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _register_node(db, node_id: str) -> None:
    """Insert a Node + (optional) NodeCertificate so the API recognises it."""
    from datetime import UTC, datetime, timedelta

    from app.models.node import Node, NodeCertificate

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
    await db.commit()


# ---------------- PUT /v1/node/config ----------------


async def test_put_node_config_persists_payload(client_with_db: httpx.AsyncClient, db) -> None:
    node_id = "test-node-config-1"
    await _register_node(db, node_id)

    cfg = {
        "config_version": "2019-09-11",
        "info": {"name": "Bulb", "fw_version": "1.0.0", "type": "Lightbulb"},
        "devices": [
            {
                "name": "Light",
                "type": "esp.device.lightbulb",
                "primary": "power",
                "params": [
                    {
                        "name": "power",
                        "type": "esp.param.power",
                        "data_type": "bool",
                        "properties": ["read", "write"],
                    }
                ],
            }
        ],
    }

    response = await client_with_db.put(
        "/v1/node/config",
        headers={"X-SSL-Client-CN": node_id},
        json=cfg,
    )
    assert response.status_code == 200, response.text

    from sqlalchemy import select

    from app.models.node import NodeConfig

    row = (await db.execute(select(NodeConfig).where(NodeConfig.node_id == node_id))).scalar_one()
    assert row.config_version == "2019-09-11"
    assert row.payload["devices"][0]["name"] == "Light"


async def test_put_node_config_without_cert_header_is_rejected(
    client_with_db: httpx.AsyncClient,
) -> None:
    response = await client_with_db.put("/v1/node/config", json={})
    assert response.status_code == 401


async def test_put_node_config_with_revoked_cert_is_rejected(
    client_with_db: httpx.AsyncClient, db
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.node import Node, NodeCertificate

    node_id = "test-node-revoked"
    db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
    db.add(
        NodeCertificate(
            serial="ser-rev",
            node_id=node_id,
            cn=node_id,
            cert_pem="",
            not_before=datetime.now(UTC),
            not_after=datetime.now(UTC) + timedelta(days=365),
            revoked=True,
        )
    )
    await db.commit()

    response = await client_with_db.put(
        "/v1/node/config",
        headers={"X-SSL-Client-CN": node_id},
        json={},
    )
    assert response.status_code in {401, 403}


# ---------------- POST /v1/node/params/local ----------------


async def test_post_node_params_local_updates_shadow(client_with_db: httpx.AsyncClient, db) -> None:
    node_id = "test-node-params"
    await _register_node(db, node_id)

    payload = {"Light": {"power": True, "brightness": 65}}
    response = await client_with_db.post(
        "/v1/node/params/local",
        headers={"X-SSL-Client-CN": node_id},
        json=payload,
    )
    assert response.status_code == 200, response.text

    from sqlalchemy import select

    from app.models.node import NodeParamsShadow

    shadow = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one()
    assert shadow.payload == payload


async def test_post_node_params_local_merges_partial_updates(
    client_with_db: httpx.AsyncClient, db
) -> None:
    node_id = "test-node-params-merge"
    await _register_node(db, node_id)

    await client_with_db.post(
        "/v1/node/params/local",
        headers={"X-SSL-Client-CN": node_id},
        json={"Light": {"power": True, "brightness": 50}},
    )
    await client_with_db.post(
        "/v1/node/params/local",
        headers={"X-SSL-Client-CN": node_id},
        json={"Light": {"brightness": 80}},
    )

    from sqlalchemy import select

    from app.models.node import NodeParamsShadow

    shadow = (
        await db.execute(select(NodeParamsShadow).where(NodeParamsShadow.node_id == node_id))
    ).scalar_one()
    assert shadow.payload["Light"]["brightness"] == 80
    assert shadow.payload["Light"]["power"] is True


# ---------------- GET /v1/node/otafetch ----------------


async def test_otafetch_returns_empty_when_no_job(client_with_db: httpx.AsyncClient, db) -> None:
    node_id = "test-node-otafetch-empty"
    await _register_node(db, node_id)

    response = await client_with_db.get(
        "/v1/node/otafetch",
        headers={"X-SSL-Client-CN": node_id},
    )
    assert response.status_code in {200, 204}
    if response.status_code == 200:
        body = response.json()
        # Empty form per firmware "wait" spec.
        assert body.get("ota_available") is False


# ---------------- POST /v1/node/otastatus ----------------


async def test_post_otastatus_without_job_returns_4xx(
    client_with_db: httpx.AsyncClient, db
) -> None:
    node_id = "test-node-otastatus-nojob"
    await _register_node(db, node_id)

    response = await client_with_db.post(
        "/v1/node/otastatus",
        headers={"X-SSL-Client-CN": node_id},
        json={"ota_job_id": "non-existent", "status": "success"},
    )
    assert response.status_code in {400, 404}
