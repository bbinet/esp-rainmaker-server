from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from app import __version__
from app.api.v1.router import api_router, v1_router
from app.core.config import get_settings
from app.core.errors import RainmakerError
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger(__name__)
    logger.info("application_starting", version=__version__, env=get_settings().env)
    yield
    logger.info("application_stopping")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ESP RainMaker (self-hosted)",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router)
    app.include_router(v1_router)

    @app.exception_handler(RainmakerError)
    async def rainmaker_handler(request: Any, exc: RainmakerError):
        return ORJSONResponse(status_code=exc.status_code, content=exc.detail)

    return app


app = create_app()
