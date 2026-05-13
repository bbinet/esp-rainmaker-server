"""Backend → device MQTT publisher.

Used by:

* `api` Deployment when the phone PUTs `/v1/user/nodes/params` — we
  publish `node/<id>/params/remote` so the device picks up the change.
* `ota-orchestrator` (Phase 6) to push `node/<id>/otaurl`.
* `api` again for one-shot commands on `node/<id>/to-node`.

The connection uses dedicated backend credentials (cert+key) granted
broad ACL on VerneMQ — distinct from device certs which are bounded to
their own namespace by vmq-authz.

In Phase 4 we ship a minimal client; Phase 5 wires it into the params
flow with a real subscriber-side echo test in compose-up e2e.
"""

from __future__ import annotations

import json
from typing import Any

import aiomqtt
import paho.mqtt.client as paho

from app.core.config import get_settings
from app.core.logging import get_logger
from app.mqtt.topics import node_topic

logger = get_logger(__name__)


async def publish_node_topic(
    *,
    node_id: str,
    suffix: str,
    payload: Any,
    qos: int = 1,
    retain: bool = False,
) -> None:
    """Publish a single message to a node-scoped topic.

    Opens a short-lived client each call. For high-throughput publish
    flows (Phase 6 OTA orchestrator), a long-running client should
    be used instead.
    """
    settings = get_settings()
    topic = node_topic(node_id, suffix)
    body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload

    try:
        async with aiomqtt.Client(
            hostname=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            username=settings.mqtt_internal_user,
            password=settings.mqtt_internal_password.get_secret_value(),
            protocol=paho.MQTTv311,  # type: ignore[arg-type]
        ) as client:
            await client.publish(topic, body, qos=qos, retain=retain)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mqtt_publish_failed", topic=topic, error=str(exc), node_id=node_id)
