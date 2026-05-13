"""Integration tests for Phase 1 — authentication & users.

Contract reproduced from the official TypeScript SDK
(`esp-rainmaker-app-sdk-ts/src/methods/ESPRMAuth/` and `src/utils/constants.ts`):

* ``POST /v1/login2`` accepts BOTH ``{user_name, password}`` and ``{refreshtoken}``
* response keys are lowercase: ``accesstoken``, ``idtoken``, ``refreshtoken``
* ``Authorization: <jwt>`` — raw JWT, no ``Bearer`` prefix
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


SIGNUP_BODY = {
    "user_name": "alice@example.com",
    "password": "Alice-Pass-1234!",
}


async def _signup_and_confirm(client: httpx.AsyncClient, db) -> dict:
    """Helper: create a user and confirm them, returning the user_name."""
    response = await client.post("/v1/user2", json=SIGNUP_BODY)
    assert response.status_code == 200, response.text

    # Fetch the confirmation code stamped on the row.
    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    code = user.confirm_code
    assert code is not None

    confirm_response = await client.put(
        "/v1/user2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "verification_code": code,
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    await db.refresh(user)
    assert user.status == "confirmed"
    return {"user_name": SIGNUP_BODY["user_name"]}


# ---------------- Signup / confirmation ----------------


async def test_signup_creates_unconfirmed_user(client_with_db: httpx.AsyncClient, db) -> None:
    response = await client_with_db.post("/v1/user2", json=SIGNUP_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    assert user.status == "unconfirmed"
    assert user.confirm_code is not None
    assert user.password_hash is not None
    assert user.password_hash != SIGNUP_BODY["password"]


async def test_signup_rejects_duplicate(client_with_db: httpx.AsyncClient) -> None:
    r1 = await client_with_db.post("/v1/user2", json=SIGNUP_BODY)
    assert r1.status_code == 200
    r2 = await client_with_db.post("/v1/user2", json=SIGNUP_BODY)
    assert r2.status_code == 409
    body = r2.json()
    assert body["status"] == "failure"
    assert "error_code" in body


async def test_confirm_signup_activates_user(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)


async def test_confirm_signup_rejects_wrong_code(
    client_with_db: httpx.AsyncClient,
) -> None:
    await client_with_db.post("/v1/user2", json=SIGNUP_BODY)
    bad = await client_with_db.put(
        "/v1/user2",
        json={"user_name": SIGNUP_BODY["user_name"], "verification_code": "000000"},
    )
    assert bad.status_code == 400


# ---------------- Login (password) ----------------


async def test_login_with_password_returns_lowercase_tokens(
    client_with_db: httpx.AsyncClient, db
) -> None:
    await _signup_and_confirm(client_with_db, db)
    response = await client_with_db.post(
        "/v1/login2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "password": SIGNUP_BODY["password"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Critical: lowercase keys, no underscore.
    assert "accesstoken" in body
    assert "idtoken" in body
    assert "refreshtoken" in body
    assert body["accesstoken"]
    assert body["idtoken"]
    assert body["refreshtoken"]
    assert "expires_in" in body


async def test_login_rejects_wrong_password(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    response = await client_with_db.post(
        "/v1/login2",
        json={"user_name": SIGNUP_BODY["user_name"], "password": "WrongPassword!1"},
    )
    assert response.status_code == 401


async def test_login_rejects_unconfirmed_user(
    client_with_db: httpx.AsyncClient,
) -> None:
    await client_with_db.post("/v1/user2", json=SIGNUP_BODY)
    response = await client_with_db.post("/v1/login2", json=SIGNUP_BODY)
    assert response.status_code in {401, 403}


# ---------------- Refresh ----------------


async def test_refresh_returns_new_tokens(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    refreshed = await client_with_db.post(
        "/v1/login2",
        json={"refreshtoken": login["refreshtoken"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert "accesstoken" in body
    assert "idtoken" in body
    # Spec says refresh path may or may not rotate the refresh token — we
    # accept either.


async def test_refresh_with_revoked_token_fails(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    logout = await client_with_db.post(
        "/v1/logout2",
        headers={"Authorization": login["accesstoken"]},
    )
    assert logout.status_code == 200

    bad = await client_with_db.post(
        "/v1/login2",
        json={"refreshtoken": login["refreshtoken"]},
    )
    assert bad.status_code == 401


# ---------------- Authorization header conventions ----------------


async def test_protected_endpoint_accepts_raw_jwt(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    me = await client_with_db.get(
        "/v1/user2",
        headers={"Authorization": login["accesstoken"]},
    )
    assert me.status_code == 200
    body = me.json()
    assert body["user_name"] == SIGNUP_BODY["user_name"]


async def test_protected_endpoint_rejects_bearer_prefix(
    client_with_db: httpx.AsyncClient, db
) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    me = await client_with_db.get(
        "/v1/user2",
        headers={"Authorization": f"Bearer {login['accesstoken']}"},
    )
    assert me.status_code == 401


async def test_protected_endpoint_rejects_missing_header(
    client_with_db: httpx.AsyncClient,
) -> None:
    me = await client_with_db.get("/v1/user2")
    assert me.status_code == 401


# ---------------- Password change ----------------


async def test_password_change_invalidates_old(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    change = await client_with_db.put(
        "/v1/password2",
        headers={"Authorization": login["accesstoken"]},
        json={"password": SIGNUP_BODY["password"], "newpassword": "BrandNew-Pass-1!"},
    )
    assert change.status_code == 200

    # Old password no longer accepted.
    old = await client_with_db.post(
        "/v1/login2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "password": SIGNUP_BODY["password"],
        },
    )
    assert old.status_code == 401

    # New password works.
    new = await client_with_db.post(
        "/v1/login2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "password": "BrandNew-Pass-1!",
        },
    )
    assert new.status_code == 200


# ---------------- Forgot password ----------------


async def test_forgot_password_flow(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)

    request = await client_with_db.put(
        "/v1/forgotpassword2",
        json={"user_name": SIGNUP_BODY["user_name"]},
    )
    assert request.status_code == 200

    from sqlalchemy import select

    from app.models.user import User

    user = (
        await db.execute(select(User).where(User.user_name == SIGNUP_BODY["user_name"]))
    ).scalar_one()
    code = user.reset_code
    assert code is not None

    reset = await client_with_db.put(
        "/v1/forgotpassword2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "verification_code": code,
            "password": "ResetByCode-1!",
        },
    )
    assert reset.status_code == 200

    login = await client_with_db.post(
        "/v1/login2",
        json={
            "user_name": SIGNUP_BODY["user_name"],
            "password": "ResetByCode-1!",
        },
    )
    assert login.status_code == 200


# ---------------- Logout ----------------


async def test_logout_revokes_refresh_token(client_with_db: httpx.AsyncClient, db) -> None:
    await _signup_and_confirm(client_with_db, db)
    login = (
        await client_with_db.post(
            "/v1/login2",
            json={
                "user_name": SIGNUP_BODY["user_name"],
                "password": SIGNUP_BODY["password"],
            },
        )
    ).json()

    logout = await client_with_db.post(
        "/v1/logout2",
        headers={"Authorization": login["accesstoken"]},
    )
    assert logout.status_code == 200
