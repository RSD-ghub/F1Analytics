"""The FIA regulations: fetching, storage and retrieval, in one place.

Kept apart from ``IngestionStore`` and the ``/data`` routes because this is a
different kind of thing. The rest of ingestion-service handles race sessions —
rows keyed by season and round, replaced wholesale when a session is re-read.
The regulations are a document corpus: chunked prose, versioned by the FIA's own
issue numbers, searched by text rather than filtered by key. Sharing a Mongo
client is the only thing the two have in common, and sharing a store class made
"all Mongo access for ingestion-service" cover two unrelated domains.

It shares a process with ingestion-service, not a purpose. PDF parsing is not
what keeps it here — ``fia_documents`` needs pdfplumber for starting grids
regardless — it is that a corpus re-read a handful of times a season does not
earn a container of its own on a 4 GB VM. If embeddings arrive and bring a
vector store or torch with them, that calculation changes, and this package is
shaped to be lifted out whole when it does: one store, one router, one source
module, and a caller already talking to it over HTTP.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, TEXT, ReplaceOne
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

#: FIA regulations, one document per numbered article. Carries a text index so
#: Bernie can find the rule that answers a question and quote it with its
#: article number — retrieval without a citation would be worse here than no
#: retrieval at all.
REGULATIONS = "regulations"

#: Named so the index can be found again and rebuilt when its weights change.
REGULATIONS_TEXT_INDEX = "regulations_text"

#: Mongo's IndexOptionsConflict — "same name, different options".
_INDEX_OPTIONS_CONFLICT = 85

#: Body-first, and measured that way rather than guessed. The first weighting
#: favoured headings 5:1 over body text and scored 1 of 6 on a small question
#: set: "how many power unit elements before a grid penalty" returned "Power
#: Unit Dynamometer", a title match beating the rule that answers it. Demoting
#: headings and promoting body took the same set to 6 of 6.
#:
#: Article keeps weight for the lookup case, though weight alone cannot carry
#: it: the index splits "B8.2.8" into "b8", "2" and "8", so a named article is
#: found by ``_article_references`` rather than by ranking.
REGULATIONS_TEXT_WEIGHTS = {"article": 5, "heading": 1, "text": 3}

#: An FIA article reference: a section letter, then dot-separated numbers —
#: ``B8.2.8``, ``C10.7.2``, ``A3.3.1``, and bare section headers like ``C2``.
#: Anchored on a word boundary so it does not fire inside ordinary prose.
_ARTICLE_REFERENCE = re.compile(r"\b([A-F]\d+(?:\.\d+)*)\b", re.IGNORECASE)


def _article_references(query: str) -> List[str]:
    """Article numbers named in a question, upper-cased, in order, deduped."""
    found: List[str] = []
    for match in _ARTICLE_REFERENCE.findall(query or ""):
        reference = match.upper()
        if reference not in found:
            found.append(reference)
    return found


class RegulationStore:
    """All Mongo access for the regulations corpus."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        """Indexes for the two ways an article is reached: by number, and by text."""
        await self._db[REGULATIONS].create_index(
            [("season", ASCENDING), ("section", ASCENDING), ("article", ASCENDING)],
            unique=True,
        )
        await self._ensure_regulations_text_index()

    async def _ensure_regulations_text_index(self) -> None:
        """Create the regulations text index, rebuilding it if the weights moved.

        Mongo will not alter a text index's options in place; asking for
        different weights under the same name is an error, not an update. So a
        weighting change made only in code applies to a fresh database and to
        nothing else — which is what happened here. The weights measured and
        committed (article 5, heading 1, text 3) sat in the source while every
        existing deployment, this developer's included, went on serving the ones
        they replaced (article 1, heading 1, text 5). Silently, because the
        conflict surfaces at startup and the search keeps working regardless.

        Dropping and recreating is cheap at this size — roughly 1,400 articles —
        and makes the weights in the source the weights in the database.
        """
        spec = [("article", TEXT), ("heading", TEXT), ("text", TEXT)]
        weights = REGULATIONS_TEXT_WEIGHTS
        try:
            await self._db[REGULATIONS].create_index(
                spec, weights=weights, name=REGULATIONS_TEXT_INDEX
            )
        except OperationFailure as exc:
            if exc.code != _INDEX_OPTIONS_CONFLICT:
                raise
            logger.info("regulations text index options changed; rebuilding")
            await self._db[REGULATIONS].drop_index(REGULATIONS_TEXT_INDEX)
            await self._db[REGULATIONS].create_index(
                spec, weights=weights, name=REGULATIONS_TEXT_INDEX
            )

    async def save_regulations(self, articles: Sequence[Any]) -> int:
        """Replace a season's articles for the sections supplied.

        Scoped to the sections in the payload rather than wiping the season, so
        re-ingesting Sporting alone does not silently delete Technical.

        **Replace means replace.** Articles present before and absent now are
        deleted, not left behind. Upserting alone made this method's name a lie
        in the one case that matters: a chunker fix that stops emitting a
        phantom article cannot remove it, so C3.1.1 — a cross-reference the
        splitter had mistaken for a header — would have survived the re-ingest
        that corrected it, and stayed searchable for ever.
        """
        if not articles:
            return 0
        operations = []
        scope: Dict[int, set] = {}
        keep: set = set()
        for article in articles:
            document = article.__dict__.copy() if hasattr(article, "__dict__") else dict(article)
            document["_id"] = "{}-{}-{}".format(
                document["season"], document["section"], document["article"]
            )
            scope.setdefault(document["season"], set()).add(document["section"])
            keep.add(document["_id"])
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )
        result = await self._db[REGULATIONS].bulk_write(operations, ordered=False)

        removed = 0
        for season, sections in scope.items():
            outcome = await self._db[REGULATIONS].delete_many({
                "season": season,
                "section": {"$in": sorted(sections)},
                "_id": {"$nin": sorted(keep)},
            })
            removed += outcome.deleted_count or 0
        if removed:
            logger.info("removed %d article(s) no longer in the source", removed)

        return (result.upserted_count or 0) + (result.modified_count or 0)

    async def search_regulations(
        self, query: str, season: Optional[int] = None, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Articles matching a question, best first.

        Mongo text search rather than embeddings, for now. Regulation vocabulary
        is precise and unusual — parc fermé, power unit element, Stop-and-Go —
        which is where lexical matching is strongest and a paraphrasing model
        adds least. No embedding provider, no vector store, no new dependency;
        embeddings can be layered over these same chunks later if retrieval
        quality turns out to be the limit.

        An article named in the question is fetched directly and returned ahead
        of the text hits. The ``article`` field carries the heaviest index weight
        precisely so that "what does B8.2.8 say" returns B8.2.8 — and against the
        real corpus it did not: the text index splits ``B8.2.8`` into ``b8``,
        ``2`` and ``8``, which then match every article sharing a number, so the
        one that was actually named came back fifth behind B8.2.5, F5.2.7 and
        C10.7.2. Weighting cannot fix that, because the token the weight applies
        to is not the reference. A lookup is not a search, so it is answered as a
        lookup.
        """
        exact: List[Dict[str, Any]] = []
        references = _article_references(query)
        if references:
            criteria: Dict[str, Any] = {"article": {"$in": references}}
            if season is not None:
                criteria["season"] = season
            exact = [
                doc
                async for doc in self._db[REGULATIONS].find(criteria, {"_id": False})
            ]

        criteria = {"$text": {"$search": query}}
        if season is not None:
            criteria["season"] = season
        cursor = (
            self._db[REGULATIONS]
            .find(criteria, {"score": {"$meta": "textScore"}, "_id": False})
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        found = [doc async for doc in cursor]

        if not exact:
            return found

        # An explicit reference outranks anything the fuzzy search turned up, by
        # construction rather than by luck: scoring it above the best text hit
        # keeps it first through any ranking or cut-off a caller applies, without
        # the caller needing to know an exact match happened.
        best = max((doc.get("score") or 0.0) for doc in found) if found else 0.0
        for doc in exact:
            doc["score"] = best + 1.0

        seen = {(doc["section"], doc["article"]) for doc in exact}
        rest = [d for d in found if (d["section"], d["article"]) not in seen]
        return (exact + rest)[:limit]
