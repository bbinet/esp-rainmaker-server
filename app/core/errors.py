"""RainMaker API error codes.

The official RainMaker API documents responses with error codes embedded
in descriptions (e.g. 100009 "Node Id is missing"). The Android SDK
inspects `status` (`success` | `failure`) and `description`; some flows
also use `error_code`. We model them centrally here.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class RainmakerError(HTTPException):
    def __init__(
        self,
        http_status: int,
        error_code: int,
        description: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "status": "failure",
            "description": description,
            "error_code": error_code,
        }
        if extra:
            body.update(extra)
        super().__init__(status_code=http_status, detail=body)
        self.error_code = error_code
        self.description = description


class ErrorCode:
    INVALID_REQUEST = 100006
    NODE_ID_MISSING = 100009
    NODES_DO_NOT_EXIST = 100015
    USER_DOES_NOT_EXIST = 100023
    NODE_NOT_MAPPED = 100036
    INVALID_NODE_CERT = 100049
    USER_ALREADY_EXISTS = 100025
    INVALID_PASSWORD = 100031
    INVALID_CREDENTIALS = 100030
    TOKEN_EXPIRED = 100051
    INVALID_TOKEN = 100052
    PERMISSION_DENIED = 100070

    OTA_IMAGE_NOT_FOUND = 104015
    OTA_JOB_NOT_FOUND = 105012
    NO_OTA_AVAILABLE = 105061

    INTERNAL_ERROR = 100099


def invalid_request(description: str = "Invalid request body") -> RainmakerError:
    return RainmakerError(status.HTTP_400_BAD_REQUEST, ErrorCode.INVALID_REQUEST, description)


def not_found(description: str, code: int = ErrorCode.NODES_DO_NOT_EXIST) -> RainmakerError:
    return RainmakerError(status.HTTP_404_NOT_FOUND, code, description)


def unauthorized(description: str = "Invalid or expired token") -> RainmakerError:
    return RainmakerError(status.HTTP_401_UNAUTHORIZED, ErrorCode.INVALID_TOKEN, description)


def forbidden(description: str = "Permission denied") -> RainmakerError:
    return RainmakerError(status.HTTP_403_FORBIDDEN, ErrorCode.PERMISSION_DENIED, description)


def conflict(description: str, code: int = ErrorCode.USER_ALREADY_EXISTS) -> RainmakerError:
    return RainmakerError(status.HTTP_409_CONFLICT, code, description)
