"""Paddock news headlines: storage, the weekend window, and search.

One document per article, keyed on a hash of its canonical link so the same
story read twice — or carried by two feeds — is stored once.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, TEXT, UpdateOne
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

NEWS = "paddock_news"
NEWS_TEXT_INDEX = "paddock_news_text"

#: Headline-first. A headline is the outlet's own statement of what the story
#: is about; the summary is a standfirst that drifts into context.
NEWS_TEXT_WEIGHTS = {"title": 3, "summary": 1}

#: Mongo's IndexOptionsConflict — "same name, different options".
_INDEX_OPTIONS_CONFLICT = 85

#: A race weekend's news runs from the Monday of race week to the Monday after.
#:
#: Race week is when the paddock stories that bear on the race are written —
#: upgrades arriving, a driver carrying an injury, a grid penalty being taken
#: on purpose. The day after catches the reaction and the stewards' late
#: decisions. Wider than this and the previous race's fallout lands in the
#: next race's record.
WEEK_BEFORE = timedelta(days=6)
DAY_AFTER = timedelta(days=1)


def weekend_window(race_start: datetime) -> Tuple[datetime, datetime]:
    """``(since, until)`` for the news belonging to a race starting at ``race_start``."""
    if race_start.tzinfo is None:
        race_start = race_start.replace(tzinfo=timezone.utc)
    return race_start - WEEK_BEFORE, race_start + DAY_AFTER


def _utc(value: Any) -> Any:
    """Motor hands back naive datetimes that are UTC; say so on the way out."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: _utc(value) for key, value in doc.items() if key != "_id"}
    out["id"] = doc.get("_id")
    return out


def _window(since: Optional[datetime], until: Optional[datetime]) -> Dict[str, Any]:
    bounds: Dict[str, Any] = {}
    if since is not None:
        bounds["$gte"] = since
    if until is not None:
        bounds["$lte"] = until
    return {"published_at": bounds} if bounds else {}


class NewsStore:
    """All Mongo access for paddock news."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[NEWS].create_index([("published_at", DESCENDING)])
        await self._db[NEWS].create_index([("source", 1), ("published_at", DESCENDING)])
        spec = [("title", TEXT), ("summary", TEXT)]
        try:
            await self._db[NEWS].create_index(
                spec, weights=NEWS_TEXT_WEIGHTS, name=NEWS_TEXT_INDEX
            )
        except OperationFailure as exc:
            # Same reasoning as the regulations index: Mongo will not alter a
            # text index's weights in place, so a change made in code would
            # otherwise apply to fresh databases only.
            if exc.code != _INDEX_OPTIONS_CONFLICT:
                raise
            await self._db[NEWS].drop_index(NEWS_TEXT_INDEX)
            await self._db[NEWS].create_index(
                spec, weights=NEWS_TEXT_WEIGHTS, name=NEWS_TEXT_INDEX
            )

    async def save(self, items: Sequence[Any]) -> int:
        """Upsert headlines; returns how many were new.

        Title and summary are refreshed on every read, because outlets do
        retitle a story as it develops and the current wording is the one a
        reader will find when they click through.

        The publication time of an undated item is written once and never
        again. Its "time" is when we first saw it, and refreshing that on every
        pass would float a story with no date to the top of every list for as
        long as the outlet kept it in the feed.
        """
        if not items:
            return 0
        operations = []
        for item in items:
            refreshed = {
                "source": item.source,
                "outlet": item.outlet,
                "title": item.title,
                "url": item.url,
                "summary": item.summary,
                "fetched_at": item.fetched_at,
            }
            first_seen: Dict[str, Any] = {"first_seen_at": item.fetched_at}
            if item.published_estimated:
                first_seen.update(published_at=item.published_at, published_estimated=True)
            else:
                refreshed.update(published_at=item.published_at, published_estimated=False)
            operations.append(UpdateOne(
                {"_id": item.item_id},
                {"$set": refreshed, "$setOnInsert": first_seen},
                upsert=True,
            ))
        result = await self._db[NEWS].bulk_write(operations, ordered=False)
        return result.upserted_count or 0

    async def prune(self, before: datetime) -> int:
        """Drop headlines published before ``before``.

        Headlines are context for a weekend, not an archive. The outlets keep
        the archive; keeping ours small keeps the text index cheap.
        """
        outcome = await self._db[NEWS].delete_many({"published_at": {"$lt": before}})
        if outcome.deleted_count:
            logger.info("pruned %d old headline(s)", outcome.deleted_count)
        return outcome.deleted_count or 0

    async def latest(
        self,
        limit: int = 20,
        source: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Newest first, optionally one outlet and one time window."""
        criteria = _window(since, until)
        if source:
            criteria["source"] = source
        cursor = (
            self._db[NEWS].find(criteria)
            .sort([("published_at", DESCENDING)])
            .limit(limit)
        )
        return [_public(doc) async for doc in cursor]

    async def search(
        self,
        query: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Headlines matching a question, best first, each with its ``score``.

        Lexical, like the regulations, and with the same weakness: it always
        returns its best match whether or not that answers anything. The
        caller decides what is good enough to show.
        """
        criteria: Dict[str, Any] = {"$text": {"$search": query}}
        criteria.update(_window(since, until))
        cursor = (
            self._db[NEWS]
            .find(criteria, {"score": {"$meta": "textScore"}})
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        return [_public(doc) async for doc in cursor]
