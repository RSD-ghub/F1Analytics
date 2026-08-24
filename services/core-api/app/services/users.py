"""User storage.

Email is the natural key and is normalised to lowercase before storage and
lookup. Without that, ``Alice@x.com`` and ``alice@x.com`` register as two
accounts and one of them can never log in — the unique index would let both
exist and the user would have no way to tell which they created.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.services.security import hash_password, verify_password

logger = logging.getLogger(__name__)

USERS = "users"


class EmailAlreadyRegistered(RuntimeError):
    """That address already has an account."""


def normalise(email: str) -> str:
    return email.strip().lower()


class UserStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        # Unique at the database, not just in application code: two concurrent
        # registrations for the same address would otherwise both pass a
        # "does this exist?" check and both insert.
        await self._db[USERS].create_index([("email", ASCENDING)], unique=True)

    async def create(self, email: str, password: str) -> Dict[str, Any]:
        document = {
            "_id": str(uuid.uuid4()),
            "email": normalise(email),
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            await self._db[USERS].insert_one(document)
        except DuplicateKeyError as exc:
            raise EmailAlreadyRegistered(
                "an account already exists for that address"
            ) from exc
        return document

    async def find(self, email: str) -> Optional[Dict[str, Any]]:
        return await self._db[USERS].find_one({"email": normalise(email)})

    async def by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        return await self._db[USERS].find_one({"_id": user_id})

    async def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify credentials.

        Runs the hash comparison even when the account does not exist, against
        a dummy hash of the same cost. Returning early on an unknown email
        makes login measurably faster for absent accounts, which turns the
        endpoint into an account-enumeration oracle.
        """
        user = await self.find(email)
        if user is None:
            verify_password(password, _TIMING_DECOY)
            return None
        if not verify_password(password, user.get("password_hash", "")):
            return None
        return user


#: A real bcrypt hash, so the decoy comparison costs the same as a live one.
#: Hashing "" at import time would work too but adds startup cost for nothing.
_TIMING_DECOY = (
    "$2b$12$eImiTXuWVxfM37uY4JANjQ.d5rQ0Ol1oNMbTn8HbLoUxXBWy1yGNa"
)
