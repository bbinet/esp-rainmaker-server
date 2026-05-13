"""OTA business logic — admin image lifecycle and job orchestration.

Tightly coupled to the firmware contract (see esp_rmaker_ota.c). The
device polls ``GET /v1/node/otafetch`` (mTLS) and expects either a
``wait`` form or a job payload with ``url``, ``ota_job_id``, ``fw_version``,
``file_md5`` and ``file_size``. The OTA orchestrator (Phase 6.5) is the
component that publishes ``node/<id>/otaurl`` via MQTT to actively push
jobs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import RainmakerError, invalid_request
from app.models.ota import OtaImage, OtaJob, OtaJobNode
from app.services.storage import get_storage


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------- Admin: image lifecycle ----------------


async def create_image_upload(
    db: AsyncSession,
    *,
    name: str,
    fw_version: str,
    file_size: int,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    image_id = uuid.uuid4()
    storage_key = f"images/{image_id}/{name}.bin"

    storage = get_storage()
    await storage.ensure_bucket(settings.minio_bucket_ota)
    upload_url = await storage.signed_upload_url(
        bucket=settings.minio_bucket_ota,
        key=storage_key,
        content_type="application/octet-stream",
    )

    db.add(
        OtaImage(
            id=image_id,
            name=name,
            fw_version=fw_version,
            file_size=file_size,
            storage_key=storage_key,
            model=model,
            status="staged",
            metadata_=metadata or {},
        )
    )
    await db.commit()
    return {
        "ota_image_id": str(image_id),
        "upload_url": upload_url,
        "storage_key": storage_key,
    }


async def confirm_image_upload(
    db: AsyncSession,
    *,
    ota_image_id: uuid.UUID,
    file_md5: str | None = None,
    file_sha256: str | None = None,
) -> OtaImage:
    image = (
        await db.execute(select(OtaImage).where(OtaImage.id == ota_image_id))
    ).scalar_one_or_none()
    if image is None:
        raise invalid_request("Unknown ota_image_id")
    image.file_md5 = file_md5
    image.file_sha256 = file_sha256
    image.status = "ready"
    await db.commit()
    return image


# ---------------- Admin: jobs ----------------


async def create_job(
    db: AsyncSession,
    *,
    name: str,
    ota_image_id: uuid.UUID,
    node_ids: list[str],
    rollout_policy: dict | None = None,
    created_by: uuid.UUID | None = None,
) -> OtaJob:
    image = (
        await db.execute(select(OtaImage).where(OtaImage.id == ota_image_id))
    ).scalar_one_or_none()
    if image is None or image.status != "ready":
        raise invalid_request("Image not ready")

    job = OtaJob(
        image_id=ota_image_id,
        name=name,
        status="active",
        rollout_policy=rollout_policy or {},
        created_by=created_by,
        started_at=_now(),
    )
    db.add(job)
    await db.flush()
    for node_id in node_ids:
        db.add(OtaJobNode(job_id=job.id, node_id=node_id, status="pending"))
    await db.commit()
    await db.refresh(job)
    return job


# ---------------- Node-side ----------------


async def fetch_pending_for_node(db: AsyncSession, *, node_id: str) -> dict[str, Any]:
    """Return the next pending OTA job for ``node_id`` as a fetch payload."""
    settings = get_settings()
    row = (
        await db.execute(
            select(OtaJobNode, OtaJob, OtaImage)
            .join(OtaJob, OtaJob.id == OtaJobNode.job_id)
            .join(OtaImage, OtaImage.id == OtaJob.image_id)
            .where(
                OtaJobNode.node_id == node_id,
                OtaJobNode.status.in_(["pending", "in-progress"]),
                OtaJob.status == "active",
            )
            .order_by(OtaJobNode.created_at.asc())
        )
    ).first()
    if row is None:
        return {
            "ota_available": False,
            "action": "wait",
            "min_wait": 60,
            "max_wait": 600,
        }
    jn, _job, image = row
    storage = get_storage()
    url = await storage.signed_download_url(bucket=settings.minio_bucket_ota, key=image.storage_key)
    return {
        "ota_available": True,
        "ota_job_id": str(jn.job_id),
        "url": url,
        "file_size": image.file_size,
        "file_md5": image.file_md5,
        "file_sha256": image.file_sha256,
        "fw_version": image.fw_version,
        "metadata": image.metadata_ or {},
    }


async def update_status(
    db: AsyncSession,
    *,
    node_id: str,
    ota_job_id: str,
    status: str,
    additional_info: str | None = None,
) -> OtaJobNode:
    try:
        job_uuid = uuid.UUID(ota_job_id)
    except (TypeError, ValueError) as exc:
        raise invalid_request("Invalid ota_job_id") from exc
    jn = (
        await db.execute(
            select(OtaJobNode).where(OtaJobNode.job_id == job_uuid, OtaJobNode.node_id == node_id)
        )
    ).scalar_one_or_none()
    if jn is None:
        raise RainmakerError(404, 105012, "OTA job not found for node")
    jn.status = status
    jn.additional_info = additional_info
    jn.last_update = _now()
    await db.commit()
    await db.refresh(jn)
    return jn


# ---------------- User-side ----------------


async def get_status_for_node(db: AsyncSession, *, node_id: str) -> dict[str, Any]:
    row = (
        await db.execute(
            select(OtaJobNode)
            .where(OtaJobNode.node_id == node_id)
            .order_by(OtaJobNode.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        return {"node_id": node_id, "status": "none"}
    return {
        "node_id": node_id,
        "ota_job_id": str(row.job_id),
        "status": row.status,
        "progress": row.progress,
        "additional_info": row.additional_info,
    }
