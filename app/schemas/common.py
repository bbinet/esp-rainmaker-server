from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SuccessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str = "success"
    description: str | None = None


class FailureResponse(BaseModel):
    status: str = "failure"
    description: str
    error_code: int


class PaginatedListMeta(BaseModel):
    total: int
    next_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    components: dict[str, str] = Field(default_factory=dict)


class ApiVersionsResponse(BaseModel):
    supported_versions: list[str]
    additional_info: dict[str, Any] = Field(default_factory=dict)
