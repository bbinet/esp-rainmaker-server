from __future__ import annotations

import time

import jwt
import pytest

from app.core.security import (
    decode_token,
    hash_password,
    mint_token,
    verify_password,
)


def test_hash_and_verify_password_roundtrip() -> None:
    h = hash_password("hunter2-Strong!")
    assert verify_password("hunter2-Strong!", h)
    assert not verify_password("hunter3", h)


def test_hash_password_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_mint_and_decode_access_token() -> None:
    token, exp = mint_token("user-abc", "access", extra_claims={"role": "user"})
    payload = decode_token(token, expected_kind="access")
    assert payload["sub"] == "user-abc"
    assert payload["token_use"] == "access"
    assert payload["role"] == "user"
    assert payload["exp"] == int(exp.timestamp())


def test_decode_rejects_wrong_kind() -> None:
    token, _ = mint_token("user-xyz", "refresh")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_kind="access")


def test_decode_expired_token_raises() -> None:
    token, _ = mint_token("user-1", "access", ttl_seconds=-1)
    # Allow clock skew tolerance to be irrelevant: sleep a tick.
    time.sleep(0.01)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_decode_tampered_signature_raises() -> None:
    token, _ = mint_token("user-2", "access")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(tampered)
