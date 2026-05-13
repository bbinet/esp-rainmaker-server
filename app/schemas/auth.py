from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Body of POST /v1/login2.

    Two shapes are accepted:

    * password login: ``user_name`` + ``password``
    * refresh:        ``refreshtoken`` alone
    * OTP request:    ``user_name`` + ``otp`` (Phase ulterior)
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    user_name: str | None = None
    password: str | None = None
    refreshtoken: str | None = None
    otp: str | None = None


class LoginResponse(BaseModel):
    """Response of POST /v1/login2.

    Field names MUST stay lowercase to match the official TypeScript SDK
    (`com.esprmbase.accessToken` storage keys, etc).
    """

    status: str = "success"
    accesstoken: str
    idtoken: str
    refreshtoken: str
    expires_in: int


class SignUpRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_name: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class ConfirmSignUpRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_name: str
    verification_code: str = Field(min_length=4, max_length=16)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password: str
    newpassword: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_name: str
    verification_code: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
