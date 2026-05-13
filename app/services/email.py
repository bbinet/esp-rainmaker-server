"""Email delivery via SMTP, with a no-op fallback for tests/dev.

In tests we don't actually send mail — the verification code is read
back from the User row by the test helpers. In dev (`smtp4dev` mock)
mail is delivered to a local viewer. In prod, real SMTP creds are used.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


async def send_confirmation_email(to: str, code: str) -> None:
    await _send(
        to,
        subject="Confirm your RainMaker account",
        body=f"Your confirmation code is: {code}",
    )


async def send_reset_email(to: str, code: str) -> None:
    await _send(
        to,
        subject="Reset your RainMaker password",
        body=f"Your password reset code is: {code}",
    )


async def _send(to: str, *, subject: str, body: str) -> None:
    s = get_settings()
    if s.env == "test":
        _logger.info("email_skipped_test_mode", to=to, subject=subject)
        return

    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_user,
            password=(s.smtp_password.get_secret_value() if s.smtp_password else None),
            start_tls=s.smtp_starttls,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("email_send_failed", to=to, error=str(exc))
        await asyncio.sleep(0)  # tick — let cancellation propagate cleanly
