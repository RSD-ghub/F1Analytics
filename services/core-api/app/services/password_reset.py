"""Password reset by emailed link.

The token is 32 random bytes, sent once by email and stored only as a SHA-256
hash, so a copy of the database cannot be used to reset anyone's password.
Tokens last 30 minutes, work once, and every outstanding token for an account
is spent when any one of them is used. A completed reset records
``password_changed_at``, which retires every login token issued before it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING

logger = logging.getLogger(__name__)

RESETS = "password_resets"
TOKEN_LIFETIME = timedelta(minutes=30)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ResetStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[RESETS].create_index([("token_hash", ASCENDING)], unique=True)
        # Mongo removes expired rows itself; the expiry check below does not
        # rely on it, since the TTL monitor runs only once a minute.
        await self._db[RESETS].create_index("expires_at", expireAfterSeconds=0)

    async def issue(self, user_id: str, now: Optional[datetime] = None) -> str:
        now = now or datetime.now(timezone.utc)
        token = secrets.token_urlsafe(32)
        await self._db[RESETS].insert_one({
            "token_hash": _digest(token),
            "user_id": user_id,
            "created_at": now,
            "expires_at": now + TOKEN_LIFETIME,
            "used": False,
        })
        return token

    async def redeem(self, token: str, now: Optional[datetime] = None) -> Optional[str]:
        """The user id the token resets, spending it and its siblings; else None."""
        now = now or datetime.now(timezone.utc)
        row = await self._db[RESETS].find_one_and_update(
            {"token_hash": _digest(token), "used": False},
            {"$set": {"used": True, "used_at": now}},
        )
        if row is None:
            return None
        expires = row["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            return None
        await self._db[RESETS].update_many(
            {"user_id": row["user_id"], "used": False},
            {"$set": {"used": True, "used_at": now}},
        )
        return row["user_id"]


class Mailer:
    """SMTP when configured; otherwise the link goes to the log.

    The log fallback is for running on a laptop with no mail server, where the
    operator is also the only user. On a public deployment set SMTP_HOST, or
    the link is visible only to whoever can read the logs.
    """

    def __init__(self, host: str, port: int, username: str, password: str,
                 sender: str, starttls: bool = True) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.starttls = sender, starttls

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

    async def send_reset(self, to: str, link: str) -> None:
        if not self.configured:
            logger.warning("SMTP not configured; password reset link for %s: %s", to, link)
            return
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = "Reset your F1 Forecast password"
        message.set_content(
            "Someone asked to reset the password for this address.\n\n"
            f"To choose a new password, open this link within 30 minutes:\n{link}\n\n"
            "If it wasn't you, ignore this email; your password has not changed.\n"
        )
        try:
            await asyncio.to_thread(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            # Logged, not raised: the response must not differ between an
            # address that exists (and failed to send) and one that does not.
            logger.error("password reset email to %s failed: %s", to, exc)
