"""The strategy guide's passages: storage, and the ranking index built over them.

Shaped like ``app.regulations.store`` — one store owning one corpus — but
ranked in-process (see ``retrieval``) rather than by a Mongo text index. The
index is built from every passage on the first search and kept until a re-ingest
moves the corpus revision on, so a search costs one tiny lookup plus arithmetic.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_common.cache import invalidates
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, ReplaceOne

from app.guide.retrieval import Index
from app.guide.source import Passage

logger = logging.getLogger(__name__)

#: One document per passage.
GUIDE = "strategy_guide"

#: One document, ``_id: "revision"``, moved on by every ingest. The cached index
#: is valid exactly as long as the revision it was built at.
GUIDE_META = "strategy_guide_meta"

#: Process-wide: every request builds a fresh store over the same database, and
#: rebuilding the index per request would throw away the reason to cache it.
_cached: Dict[str, Tuple[Optional[str], Index]] = {}


class GuideStore:
    """All Mongo access for the strategy guide."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[GUIDE].create_index(
            [("source", ASCENDING), ("chapter", ASCENDING), ("index", ASCENDING)],
            unique=True,
        )

    @invalidates("guide")
    async def save_book(self, passages: Sequence[Passage]) -> int:
        """Replace one book's passages. Passages no longer produced are deleted.

        Replace means replace, for the reason it does in the regulations: a
        chunker fix that stops emitting a passage must be able to remove it.
        """
        if not passages:
            return 0
        sources = {p.source for p in passages}
        keep = [p.key for p in passages]
        result = await self._db[GUIDE].bulk_write(
            [ReplaceOne({"_id": p.key}, p.to_document(), upsert=True) for p in passages],
            ordered=False,
        )
        removed = await self._db[GUIDE].delete_many(
            {"source": {"$in": sorted(sources)}, "_id": {"$nin": keep}}
        )
        if removed.deleted_count:
            logger.info("removed %d passage(s) no longer produced", removed.deleted_count)
        await self._db[GUIDE_META].replace_one(
            {"_id": "revision"},
            {"_id": "revision", "value": uuid.uuid4().hex,
             "ingested_at": datetime.now(timezone.utc)},
            upsert=True,
        )
        return (result.upserted_count or 0) + (result.modified_count or 0)

    async def _revision(self) -> Optional[str]:
        meta = await self._db[GUIDE_META].find_one({"_id": "revision"})
        return (meta or {}).get("value")

    async def _index(self) -> Index:
        revision = await self._revision()
        held = _cached.get(self._db.name)
        if held and held[0] == revision:
            return held[1]
        passages = [
            doc async for doc in self._db[GUIDE].find({}).sort(
                [("source", ASCENDING), ("chapter", ASCENDING), ("index", ASCENDING)]
            )
        ]
        index = Index.build(passages)
        _cached[self._db.name] = (revision, index)
        return index

    async def search(
        self, query: str, limit: int = 5, chapters: Sequence[int] = ()
    ) -> List[Dict[str, Any]]:
        """Passages best matching a question, best first, each with its ``score``."""
        return (await self._index()).search(query, limit=limit, chapters=chapters)

    async def status(self) -> Dict[str, Any]:
        """Which books are held and how many passages each, for /guide/status."""
        books = [
            {"source": row["_id"]["source"], "title": row["_id"]["title"],
             "author": row["_id"]["author"], "passages": row["passages"],
             "chapters": len(row["chapters"])}
            async for row in self._db[GUIDE].aggregate([
                {"$group": {
                    "_id": {"source": "$source", "title": "$title", "author": "$author"},
                    "passages": {"$sum": 1},
                    "chapters": {"$addToSet": "$chapter"},
                }},
            ])
        ]
        meta = await self._db[GUIDE_META].find_one({"_id": "revision"}) or {}
        return {"books": books, "ingested_at": meta.get("ingested_at")}
