from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.db.session import AsyncSessionLocal
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz")
async def readyz(response: Response) -> HealthResponse:
    components: dict[str, str] = {}
    db_ok = True
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        components["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        components["db"] = f"error: {exc!s}"

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        components=components,
    )
