"""Topic-based routing for inbound MQTT messages.

Used by `mqtt-ingestor` after it subscribes to ``node/+/+`` on VerneMQ
and receives each PUBLISH. Each topic suffix has a dedicated handler.

The handlers commit their own writes via the provided AsyncSession.
Unknown topics are dropped silently — we don't want a noisy peer to
crash the ingestor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.node import Node
from app.models.user_node import MappingChallenge, UserNodeMapping
from app.services import node as node_service


async def _ensure_node(db: AsyncSession, node_id: str) -> None:
    """Defensive create-if-absent — the FK guards `node_configs` etc.

    In production the Node row is created at /claim/verify time so this
    is a no-op; in tests and for unexpected republishes (the device's
    state outlives ours) we recreate the row.
    """
    existing = (await db.execute(select(Node).where(Node.node_id == node_id))).scalar_one_or_none()
    if existing is None:
        db.add(Node(node_id=node_id, registration_ts=datetime.now(UTC)))
        await db.commit()


logger = get_logger(__name__)


class _MessageLike(Protocol):
    @property
    def topic(self) -> Any: ...  # may be str or a TopicLike with `.value`
    @property
    def payload(self) -> bytes: ...


def _topic_str(topic_obj: Any) -> str:
    """aiomqtt yields a Topic object with `.value`; tests pass plain strings."""
    return getattr(topic_obj, "value", topic_obj)


def _split_topic(topic: str) -> tuple[str, str] | None:
    """Return ``(node_id, suffix)`` for ``node/<id>/<suffix>``."""
    if not topic.startswith("node/"):
        return None
    parts = topic.split("/", 2)
    if len(parts) < 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _parse_payload(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


async def dispatch(db: AsyncSession, message: _MessageLike) -> None:
    topic = _topic_str(message.topic)
    parsed = _split_topic(topic)
    if parsed is None:
        return
    node_id, suffix = parsed
    payload = _parse_payload(message.payload)
    if payload is None:
        logger.warning("mqtt_payload_unparseable", topic=topic)
        return

    handler = _HANDLERS.get(suffix)
    if handler is None:
        logger.debug("mqtt_topic_ignored", topic=topic, suffix=suffix)
        return
    try:
        await handler(db, node_id, payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("mqtt_handler_failed", topic=topic, error=str(exc))


# ---------------- Handlers ----------------


async def _handle_config(db: AsyncSession, node_id: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    await _ensure_node(db, node_id)
    await node_service.upsert_node_config(db, node_id=node_id, payload=payload)


async def _handle_params_local(db: AsyncSession, node_id: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    await _ensure_node(db, node_id)
    await node_service.upsert_params_shadow(db, node_id=node_id, params=payload)


async def _handle_user_mapping(db: AsyncSession, node_id: str, payload: dict) -> None:
    """Match a `node/<id>/user/mapping` PUBLISH against pending challenges.

    The device echoes back `{node_id, user_id, secret_key, reset}`. We
    pair the secret_key with a pending MappingChallenge owned by the
    same user and create the UserNodeMapping. The challenge transitions
    to `completed` so subsequent publishes are no-ops.
    """
    if not isinstance(payload, dict):
        return
    secret_key = payload.get("secret_key")
    if not secret_key:
        return
    if payload.get("node_id") and payload["node_id"] != node_id:
        # CN-vs-payload mismatch — device lying about its identity. Drop.
        logger.warning(
            "mqtt_user_mapping_node_id_mismatch",
            topic_node_id=node_id,
            payload_node_id=payload["node_id"],
        )
        return

    challenge = (
        await db.execute(
            select(MappingChallenge).where(
                MappingChallenge.secret_key == secret_key,
                MappingChallenge.node_id == node_id,
                MappingChallenge.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        return
    if challenge.expires_at < datetime.now(UTC):
        challenge.status = "expired"
        await db.commit()
        return

    existing = (
        await db.execute(
            select(UserNodeMapping).where(
                UserNodeMapping.user_id == challenge.user_id,
                UserNodeMapping.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            UserNodeMapping(
                user_id=challenge.user_id,
                node_id=node_id,
                role="primary",
                primary=True,
            )
        )
    challenge.status = "completed"
    challenge.completed_at = datetime.now(UTC)
    await db.commit()


async def _handle_otastatus(db: AsyncSession, node_id: str, payload: dict) -> None:
    """Phase 6 will wire job status updates. For now: no-op."""
    _ = (db, node_id, payload)


async def _handle_tsdata(db: AsyncSession, node_id: str, payload: dict) -> None:
    """Bulk ts_data form: { ts_data: [{name, dt, records: [{t, v}, ...]}, ...] }."""
    if not isinstance(payload, dict):
        return
    series = payload.get("ts_data")
    if not isinstance(series, list):
        return
    from app.services import tsdata as tsdata_service

    for entry in series:
        full_name = entry.get("name", "")
        if "." not in full_name:
            continue
        device_name, param_name = full_name.split(".", 1)
        dt = entry.get("dt", "int")
        records = entry.get("records") or []
        points: list = []
        for r in records:
            try:
                ts = datetime.fromtimestamp(int(r["t"]), tz=UTC)
            except (TypeError, ValueError, KeyError):
                continue
            points.append((ts, r.get("v")))
        if points:
            await tsdata_service.insert_tsdata(
                db,
                node_id=node_id,
                device_name=device_name,
                param_name=param_name,
                data_type=dt,
                points=points,
            )


async def _handle_simple_tsdata(db: AsyncSession, node_id: str, payload: dict) -> None:
    """Simple form: { name, dt, t, v }."""
    if not isinstance(payload, dict):
        return
    full_name = payload.get("name", "")
    if "." not in full_name:
        return
    device_name, param_name = full_name.split(".", 1)
    dt = payload.get("dt", "int")
    try:
        ts = datetime.fromtimestamp(int(payload["t"]), tz=UTC)
    except (TypeError, ValueError, KeyError):
        return

    from app.services import tsdata as tsdata_service

    await tsdata_service.insert_tsdata(
        db,
        node_id=node_id,
        device_name=device_name,
        param_name=param_name,
        data_type=dt,
        points=[(ts, payload.get("v"))],
    )


async def _handle_alert(db: AsyncSession, node_id: str, payload: dict) -> None:
    """Phase 9 (push) will fan-out alerts. For now: no-op."""
    _ = (db, node_id, payload)


from app.mqtt import topics as _t  # noqa: E402

_HANDLERS = {
    _t.CONFIG: _handle_config,
    _t.PARAMS_LOCAL: _handle_params_local,
    _t.PARAMS_LOCAL_INIT: _handle_params_local,
    _t.USER_MAPPING: _handle_user_mapping,
    _t.OTASTATUS: _handle_otastatus,
    _t.TSDATA: _handle_tsdata,
    _t.SIMPLE_TSDATA: _handle_simple_tsdata,
    _t.ALERT: _handle_alert,
}
