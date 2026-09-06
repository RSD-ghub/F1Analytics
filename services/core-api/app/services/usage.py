"""Budgets and caching for LLM calls, so a public URL cannot become a bill.

Two different problems, deliberately solved differently.

**Explanations are shared.** "Why did the model favour Russell at Monza?" has one
answer for a given locked forecast, and that forecast is immutable — so the
answer is too. Generating it once and serving it from Mongo makes the cost of an
explanation O(predictions) rather than O(visitors), which is what allows it to be
offered at all rather than metered per reader.

**Conversations are personal.** A thread is unique to whoever is holding it and
cannot be shared or replayed, so there is nothing to cache and the only control
is a budget. Per-user first, because one abusive account should not deny everyone
else; a global ceiling behind it, because per-user limits multiply by however
many accounts someone can register.

The global ceiling fails *closed*: if the counter cannot be read, calls are
refused rather than allowed. An unavailable budget check is not permission to
spend.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pymongo import ASCENDING

logger = logging.getLogger(__name__)

USAGE = "llm_usage"
CACHE = "ai_cache"

#: Cached explanations expire so a model or prompt change eventually reaches
#: readers. Long, because the underlying prediction never changes.
CACHE_TTL = timedelta(days=30)


class BudgetExceeded(RuntimeError):
    """This caller, or the deployment as a whole, has spent its allowance."""

    def __init__(self, message: str, scope: str) -> None:
        super().__init__(message)
        self.scope = scope


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class UsageStore:
    def __init__(self, database) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[USAGE].create_index(
            [("day", ASCENDING), ("subject", ASCENDING)], unique=True
        )
        await self._db[CACHE].create_index([("key", ASCENDING)], unique=True)

    # ── Budget ───────────────────────────────────────────────────────────────

    async def consume(self, user_id: str, per_user: int, per_day: int) -> None:
        """Record one call, or raise ``BudgetExceeded``.

        Increments before the call rather than after: a request that fails
        upstream has still cost us the attempt, and counting only successes
        would let a caller retry a failing prompt without limit.
        """
        day = _today()
        if per_user > 0:
            used = await self._bump(day, "user:{}".format(user_id))
            if used > per_user:
                raise BudgetExceeded(
                    "you have used your {} Bernie messages for today; the limit "
                    "resets at midnight UTC".format(per_user),
                    scope="user",
                )
        if per_day > 0:
            total = await self._bump(day, "global")
            if total > per_day:
                raise BudgetExceeded(
                    "Bernie has reached the deployment's daily message limit; "
                    "it resets at midnight UTC",
                    scope="global",
                )

    async def _bump(self, day: str, subject: str) -> int:
        document = await self._db[USAGE].find_one_and_update(
            {"day": day, "subject": subject},
            {"$inc": {"calls": 1}},
            upsert=True,
            return_document=True,
        )
        return int((document or {}).get("calls", 0))

    async def usage_today(self, user_id: str) -> Dict[str, int]:
        day = _today()
        rows = self._db[USAGE].find({"day": day, "subject": {"$in": [
            "user:{}".format(user_id), "global"]}})
        found = {row["subject"]: int(row.get("calls", 0)) async for row in rows}
        return {
            "user": found.get("user:{}".format(user_id), 0),
            "global": found.get("global", 0),
        }

    # ── Cache ────────────────────────────────────────────────────────────────

    @staticmethod
    def key(*parts: Any) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def cached(self, key: str) -> Optional[Dict[str, Any]]:
        document = await self._db[CACHE].find_one({"key": key}, {"_id": False})
        if not document:
            return None
        created = document.get("created_at")
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created > CACHE_TTL:
                return None
        return document.get("value")

    async def store(self, key: str, value: Dict[str, Any]) -> None:
        await self._db[CACHE].update_one(
            {"key": key},
            {"$set": {"key": key, "value": value,
                      "created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
