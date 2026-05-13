"""Entrypoint for the `worker` Deployment.

Runs procrastinate workers against the Postgres-backed queue. Tasks are
registered through `app.workers` (added phase-by-phase).
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger


async def run() -> None:
    configure_logging()
    logger = get_logger(__name__)
    logger.info("worker_started_stub", note="procrastinate wiring lands in Phase 1+")
    while True:
        await asyncio.sleep(60)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
