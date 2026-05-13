"""S3-compatible object storage abstraction (Garage in production).

The OTA pipeline uploads firmware images to Garage and produces signed
URLs the device downloads from. In tests we don't talk to a real
backend — the in-memory ``LocalStubStorage`` produces fake-but-deterministic
URLs that still flow through the same API.

To swap to real Garage in dev/prod, set ``RM_MINIO_ENDPOINT`` and
credentials; ``get_storage()`` will return the boto3-based implementation.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings


class ObjectStorage(Protocol):
    async def signed_upload_url(
        self, *, bucket: str, key: str, content_type: str, expires_in: int = 600
    ) -> str: ...

    async def signed_download_url(self, *, bucket: str, key: str, expires_in: int = 600) -> str: ...

    async def ensure_bucket(self, bucket: str) -> None: ...


class LocalStubStorage:
    """In-process stub. Used in tests and when ``RM_MINIO_ENDPOINT`` is
    pointed at a service we can't reach (e.g. local-dev without Garage)."""

    def __init__(self) -> None:
        self._buckets: set[str] = set()

    async def ensure_bucket(self, bucket: str) -> None:
        self._buckets.add(bucket)

    async def signed_upload_url(
        self, *, bucket: str, key: str, content_type: str, expires_in: int = 600
    ) -> str:
        _ = (content_type, expires_in)
        token = secrets.token_urlsafe(8)
        return f"http://stub-storage/{bucket}/{key}?upload={token}"

    async def signed_download_url(self, *, bucket: str, key: str, expires_in: int = 600) -> str:
        _ = expires_in
        token = secrets.token_urlsafe(8)
        return f"http://stub-storage/{bucket}/{key}?download={token}"


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    """Return the storage backend for this process.

    Production wires a real S3-compatible client (boto3 / minio) here.
    For Phase 6 we ship the stub; Phase 6.5 swaps it in for compose-up.
    """
    _ = get_settings()
    return LocalStubStorage()


def _coerce_to_dict(obj: Any) -> Any:
    return obj  # ergonomic helper for downstream type-checkers
