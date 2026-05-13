"""S3-compatible object storage abstraction (Garage in production).

Two backends:

* **LocalStubStorage** — in-process, returns fake but deterministic URLs.
  Used in the test suite and when ``RM_MINIO_ENDPOINT`` is unset or
  empty. Keeps tests fast and hermetic.

* **MinioS3Storage** — production-ish backend that talks S3 v4 against
  Garage / MinIO / AWS S3. Selected automatically when the endpoint is
  reachable. Issues real presigned PUT / GET URLs the firmware can hit.

The dispatching logic is in ``get_storage()`` and respects
``RM_FEATURE_S3_REAL`` (``"true"`` to force the real backend).
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class ObjectStorage(Protocol):
    async def signed_upload_url(
        self, *, bucket: str, key: str, content_type: str, expires_in: int = 600
    ) -> str: ...

    async def signed_download_url(self, *, bucket: str, key: str, expires_in: int = 600) -> str: ...

    async def ensure_bucket(self, bucket: str) -> None: ...


class LocalStubStorage:
    """In-process stub used by the test suite."""

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


class MinioS3Storage:
    """Real S3 backend (Garage, MinIO, AWS S3).

    The presigned URL host is rewritten from the internal endpoint
    (``RM_MINIO_ENDPOINT``, e.g. ``garage:3900``) to a public-facing one
    (``RM_MINIO_PUBLIC_ENDPOINT``, e.g. ``localhost:3900``) so the
    device — which lives outside the cluster network — can hit the URL.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        region: str,
        public_endpoint: str | None = None,
        public_secure: bool | None = None,
    ) -> None:
        from minio import Minio

        # Internal client — used for bucket admin operations from within
        # the compose / k8s network.
        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )
        # Public client — signs URLs with the externally-resolvable
        # host so devices (which can't reach `garage:3900`) hit the
        # right endpoint. S3 v4 signs the Host header so we must sign
        # with the public host from the start, not rewrite afterwards.
        pub_ep = public_endpoint or endpoint
        pub_secure = public_secure if public_secure is not None else secure
        self._public_client = Minio(
            endpoint=pub_ep,
            access_key=access_key,
            secret_key=secret_key,
            secure=pub_secure,
            region=region,
        )

    async def ensure_bucket(self, bucket: str) -> None:
        import anyio

        def _run() -> None:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)

        await anyio.to_thread.run_sync(_run)

    async def signed_upload_url(
        self, *, bucket: str, key: str, content_type: str, expires_in: int = 600
    ) -> str:
        import anyio
        from datetime import timedelta

        def _run() -> str:
            return self._public_client.presigned_put_object(
                bucket, key, expires=timedelta(seconds=expires_in)
            )

        _ = content_type
        return await anyio.to_thread.run_sync(_run)

    async def signed_download_url(self, *, bucket: str, key: str, expires_in: int = 600) -> str:
        import anyio
        from datetime import timedelta

        def _run() -> str:
            return self._public_client.presigned_get_object(
                bucket, key, expires=timedelta(seconds=expires_in)
            )

        return await anyio.to_thread.run_sync(_run)


def _real_storage_requested() -> bool:
    """Real S3 is opt-in: set ``RM_FEATURE_S3_REAL=true`` to enable.

    Tests and `pytest` runs leave it unset → stub backend.
    `docker compose up` sets it explicitly once Garage is initialised
    (via the `scripts/init_garage.sh` flow).
    """
    if os.environ.get("RM_FEATURE_S3_REAL", "false").lower() != "true":
        return False
    s = get_settings()
    return bool(s.minio_endpoint.strip())


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    s = get_settings()
    if not _real_storage_requested():
        _logger.info("storage_backend", backend="local-stub")
        return LocalStubStorage()
    public_endpoint = os.environ.get("RM_MINIO_PUBLIC_ENDPOINT", s.minio_endpoint)
    public_secure_env = os.environ.get("RM_MINIO_PUBLIC_SECURE")
    public_secure = None
    if public_secure_env is not None:
        public_secure = public_secure_env.lower() == "true"
    _logger.info(
        "storage_backend",
        backend="minio-s3",
        endpoint=s.minio_endpoint,
        public_endpoint=public_endpoint,
    )
    return MinioS3Storage(
        endpoint=s.minio_endpoint,
        access_key=s.minio_access_key.get_secret_value(),
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
        region=s.minio_region,
        public_endpoint=public_endpoint,
        public_secure=public_secure,
    )
