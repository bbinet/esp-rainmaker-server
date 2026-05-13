"""Entrypoint for the `vmq-authz` Deployment.

A tiny FastAPI process serving only the VerneMQ webhook routes. Same
code as in the main `api` app — but isolated for latency and blast-
radius reasons (VerneMQ blocks on each hook call).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse

from app import __version__
from app.api.internal import vmq_authz as vmq_authz_router
from app.core.errors import RainmakerError
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ = app
    configure_logging()
    yield


def create_authz_app() -> FastAPI:
    app = FastAPI(
        title="rainmaker-vmq-authz",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(vmq_authz_router.router)

    @app.exception_handler(RainmakerError)
    async def rainmaker_handler(request: Request, exc: RainmakerError) -> ORJSONResponse:
        _ = request
        return ORJSONResponse(status_code=exc.status_code, content=exc.detail)

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
