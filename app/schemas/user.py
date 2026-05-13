from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID = Field(validation_alias="id")
    user_name: str
    email: str
    full_name: str | None = None
    phone_number: str | None = None
    status: str
    mfa_enabled: bool = False
    is_super_admin: bool = False
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str | None = None
    phone_number: str | None = None
