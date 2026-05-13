"""Entrypoint for the `vmq-authz` Deployment.

Tiny FastAPI app that answers VerneMQ `vmq_webhooks` hooks. Latency is
the priority: the broker blocks on each MQTT operation waiting for our
response, so the service is intentionally minimal.

Full hook handlers land in Phase 2.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from app import __version__


def create_authz_app() -> FastAPI:
    app = FastAPI(title="rainmaker-vmq-authz", version=__version__)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/on_register")
    async def on_register() -> dict[str, str]:
        # TODO(Phase 2): extract CN from peer certificate, check revocation.
        return {"result": "next"}

    @app.post("/auth/on_publish")
    async def on_publish() -> dict[str, str]:
        # TODO(Phase 2): enforce `node/<cn>/#` namespace.
        return {"result": "next"}

    @app.post("/auth/on_subscribe")
    async def on_subscribe() -> dict[str, str]:
        return {"result": "next"}

    return app


authz_app = create_authz_app()


def main() -> None:
    uvicorn.run(
        "app.entrypoints.vmq_authz:authz_app",
        host="0.0.0.0",  # noqa: S104
        port=8001,
        log_config=None,
        access_log=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
