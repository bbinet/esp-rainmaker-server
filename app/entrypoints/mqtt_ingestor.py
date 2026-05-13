"""Entrypoint for the `mqtt-ingestor` Deployment.

Long-running daemon: subscribes to ``node/+/#`` on VerneMQ with the
backend credentials and dispatches each PUBLISH to the topic router.

MQTT 3.1.1 is forced because:
- the ESP firmware (paho-c) is MQTT 3.1.1 by default; matching protocol
  keeps the topic / will / retain semantics aligned;
- VerneMQ's ``vmq_webhooks`` plugin we use registers hooks for the v3
  family (``auth_on_register`` etc), not the M5 variants — pinning
  v3.1.1 here ensures hooks always fire.
"""

from __future__ import annotations

import asyncio

import aiomqtt
import paho.mqtt.client as paho

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.mqtt.router import dispatch


async def run() -> None:
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_broker_host,
                port=settings.mqtt_broker_port,
                username=settings.mqtt_internal_user,
                password=settings.mqtt_internal_password.get_secret_value(),
                identifier="rainmaker-backend-ingestor",
                clean_session=False,
                protocol=paho.MQTTv311,  # type: ignore[arg-type]
            ) as client:
                # `node/+/#` covers any sub-suffix depth: `node/<id>/config`,
                # `node/<id>/params/local`, `node/<id>/params/local/init`,
                # `node/<id>/diagnostics/from-node`, etc.
                await client.subscribe("node/+/#", qos=1)
                logger.info(
                    "mqtt_ingestor_connected",
                    broker=f"{settings.mqtt_broker_host}:{settings.mqtt_broker_port}",
                )
                async for message in client.messages:
                    async with AsyncSessionLocal() as session:
                        try:
                            await dispatch(session, message)
                        except Exception as exc:  # noqa: BLE001
                            logger.error("ingestor_dispatch_failed", error=str(exc))
                            await session.rollback()
        except aiomqtt.MqttError as exc:
            logger.warning("mqtt_ingestor_disconnected", error=str(exc))
            await asyncio.sleep(5)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
