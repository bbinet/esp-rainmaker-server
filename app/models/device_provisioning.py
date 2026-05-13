from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DeviceProvisioning(Base, TimestampMixin):
    """Manufacturer-provisioned per-device data.

    Each ESP32 with HMAC-capable eFuse ships from the factory with a
    secret key burned into eFuse. The factory line also uploads that key
    here against the device's MAC, enabling self-claim (HMAC challenge)
    without a user token.
    """

    __tablename__ = "device_provisioning"

    mac_addr: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    hmac_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ClaimChallenge(Base, TimestampMixin):
    """A pending /claim/initiate awaiting /claim/verify.

    Two shapes share the table:

    * **Self-claim**: ``auth_id`` + ``challenge`` issued; on verify the
      device proves possession of the HMAC key.
    * **Assisted**: ``node_id`` issued up-front; verify just signs the CSR
      after re-authenticating the user.
    """

    __tablename__ = "claim_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    mac_addr: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    challenge: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
