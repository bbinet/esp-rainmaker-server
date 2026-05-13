"""Admin OTA endpoints (image upload, job creation)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.admin import require_admin
from app.api.v1.deps.db import get_db
from app.core.errors import invalid_request
from app.models.user import User
from app.services import ota as ota_service

router = APIRouter(tags=["ota-admin"])


@router.post("/admin/otaimage/upload_request")
async def admin_upload_request(
    payload: dict,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = admin
    name = payload.get("name")
    fw_version = payload.get("fw_version")
    file_size = payload.get("file_size")
    if not name or not fw_version or not file_size:
        raise invalid_request("name, fw_version and file_size required")
    return await ota_service.create_image_upload(
        db,
        name=name,
        fw_version=fw_version,
        file_size=int(file_size),
        model=payload.get("model"),
        metadata=payload.get("metadata"),
    )


@router.post("/admin/otaimage/upload_confirm")
async def admin_upload_confirm(
    payload: dict,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = admin
    try:
        image_id = uuid.UUID(payload["ota_image_id"])
    except (KeyError, ValueError) as exc:
        raise invalid_request("ota_image_id required") from exc
    image = await ota_service.confirm_image_upload(
        db,
        ota_image_id=image_id,
        file_md5=payload.get("file_md5"),
        file_sha256=payload.get("file_sha256"),
    )
    return {"status": "success", "ota_image_id": str(image.id), "image_status": image.status}


@router.post("/admin/otajob")
async def admin_create_otajob(
    payload: dict,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    name = payload.get("name")
    try:
        image_id = uuid.UUID(payload["ota_image_id"])
    except (KeyError, ValueError) as exc:
        raise invalid_request("ota_image_id required") from exc
    nodes = payload.get("nodes") or []
    if not name or not nodes:
        raise invalid_request("name and nodes required")
    job = await ota_service.create_job(
        db,
        name=name,
        ota_image_id=image_id,
        node_ids=list(nodes),
        rollout_policy=payload.get("rollout_policy"),
        created_by=admin.id,
    )
    return {"status": "success", "ota_job_id": str(job.id)}
