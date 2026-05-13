"""Entrypoint for the `mqtt-ingestor` Deployment.

Long-running daemon: subscribes to `node/+/+` on VerneMQ with backend
credentials and persists messages to Postgres via topic-based handlers.

The full implementation lands in Phase 4.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger


async def run() -> None:
    configure_logging()
    logger = get_logger(__name__)
    logger.info("mqtt_ingestor_started_stub", note="full subscriber lands in Phase 4")
    # Keep the pod alive so k8s readiness/liveness probes pass.
    while True:
        await asyncio.sleep(60)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
