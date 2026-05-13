from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.common import ApiVersionsResponse

router = APIRouter(tags=["meta"])


@router.get("/apiversions", response_model=ApiVersionsResponse)
async def apiversions() -> ApiVersionsResponse:
    return ApiVersionsResponse(
        supported_versions=["v1"],
        additional_info={"public_base_url": get_settings().public_base_url},
    )


@router.get("/mqtt_host")
async def mqtt_host() -> dict[str, str]:
    s = get_settings()
    return {
        "mqtt_host": f"{s.mqtt_broker_host}:{s.mqtt_broker_port}",
        "mqtt_ws_host": f"{s.mqtt_ws_host}:{s.mqtt_ws_port}",
    }
