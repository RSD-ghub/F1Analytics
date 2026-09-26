"""Mongo persistence for ingested data and completeness bookkeeping.

Two guarantees this layer is responsible for:

**Idempotence.** Every row carries a deterministic ``stable_id`` used as its
Mongo ``_id``, so re-ingesting a session replaces rows in place. Healing a gap is
therefore just "run that session again" — no dedupe pass, no unique-index
violations, no fear of running a backfill twice.

**Convergence.** Upserting alone would leave behind rows from a previous, worse
ingest of the same session (a lap that turned out to be spurious, a driver whose
name resolved differently). Each write is tagged with a run id and anything left
carrying an older tag for that session is deleted, so the stored session matches
the latest ingest exactly rather than accumulating the union of every attempt.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, DeleteMany, ReplaceOne, UpdateOne

from app.models.schemas import (
    ExpectedSession,
    IngestDepth,
    SessionIngestState,
    SessionPayload,
)

logger = logging.getLogger(__name__)

#: Payload attribute → collection name. Iterating this keeps "save everything"
#: honest: adding a dataset to ``SessionPayload`` and forgetting to persist it
#: would otherwise be a silent data loss.
DATA_COLLECTIONS = {
    "results": "results",
    "laps": "laps",
    "stints": "stints",
    "pit_stops": "pit_stops",
    "weather": "weather",
    "race_control": "race_control",
    "telemetry": "telemetry",
}

EXPECTED_SESSIONS = "expected_sessions"
SESSION_STATE = "session_ingest_state"

#: Qualifying is stored outside ``DATA_COLLECTIONS`` on purpose. ``save_payload``
#: treats an empty dataset as "delete what was there", which is right for race
#: data — but qualifying is ingested on its own schedule, often days before the
#: race, and folding it into the race payload would wipe the grid every time a
#: race was re-ingested.
QUALIFYING = "qualifying"

#: Practice long-run pace. Outside DATA_COLLECTIONS for the same reason as
#: qualifying: it is ingested on its own schedule, days before the race, and a
#: race re-ingest must not wipe it.
PRACTICE = "practice_pace"

#: Forward-looking session timetables.
WEEKENDS = "race_weekends"

#: Car number -> who drives it, per season. The last standing dependency on
#: FastF1 being reachable at all.
#:
#: Qualifying has three sources, but two of them supply order and times only —
#: the FIA and jolpica both print names their own way ("Sergio PEREZ" for
#: "Sergio Pérez"), and joining history on a re-cased name does not fail, it
#: silently invents a driver with no record and poisons every feature join after
#: it. So identity always came from FastF1's entry list, and if FastF1 was down
#: the other two sources could not be used at all.
#:
#: This is that entry list, remembered. It is written whenever FastF1 answers,
#: so the names in it are the corpus's own canonical spellings rather than a
#: second opinion about them.
DRIVER_REGISTRY = "driver_registry"

#: Structured facts extracted from race control text. Also outside
#: ``DATA_COLLECTIONS``: extraction is best-effort enrichment that runs after a
#: session is already stored, and must never participate in completeness.
SIGNALS = "extracted_signals"

#: Track geometry, one document per circuit. Outside ``DATA_COLLECTIONS``
#: because it is not a session: a circuit is derived once, keyed by the track
#: rather than by season and round, and a missing map must never make a
#: weekend look incompletely ingested.
CIRCUITS = "circuits"

#: Everything the read API may serve.
READABLE_COLLECTIONS = dict(
    DATA_COLLECTIONS, qualifying=QUALIFYING, signals=SIGNALS, practice=PRACTICE
)

#: Field holding the ingest run id. Underscore-prefixed to mark it as
#: bookkeeping rather than F1 data.
RUN_FIELD = "_run"


class IngestionStore:
    """All Mongo access for ingestion-service."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    # ── Indexes ──────────────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """Indexes for the queries this service actually serves.

        Every data collection is queried by season+round (session replacement,
        gap healing) and most read paths filter by season, so both are indexed.
        """
        for collection in READABLE_COLLECTIONS.values():
            await self._db[collection].create_index(
                [("season", ASCENDING), ("round", ASCENDING)]
            )

        await self._db[WEEKENDS].create_index(
            [("season", ASCENDING), ("round", ASCENDING)], unique=True
        )
        await self._db[DRIVER_REGISTRY].create_index(
            [("season", ASCENDING), ("driver_number", ASCENDING)], unique=True
        )
        # "What is the next race?" scans forward on race start.
        await self._db[WEEKENDS].create_index([("race_start_utc", ASCENDING)])

        await self._db[EXPECTED_SESSIONS].create_index(
            [("season", ASCENDING), ("round", ASCENDING)], unique=True
        )
        await self._db[SESSION_STATE].create_index(
            [("season", ASCENDING), ("round", ASCENDING)], unique=True
        )
        # The gap query — "what is not complete?" — runs on every status check.
        await self._db[SESSION_STATE].create_index([("state", ASCENDING)])

    # ── Expected-session manifest ────────────────────────────────────────────

    async def upsert_expected_sessions(
        self, sessions: Sequence[ExpectedSession]
    ) -> int:
        """Record what the calendar says should exist.

        Written before any ingest is attempted, so a session that can never be
        fetched still leaves evidence that it was supposed to be there.
        """
        if not sessions:
            return 0

        operations = [
            ReplaceOne(
                {"season": session.season, "round": session.round},
                session.model_dump(mode="json"),
                upsert=True,
            )
            for session in sessions
        ]
        result = await self._db[EXPECTED_SESSIONS].bulk_write(operations, ordered=False)
        return result.upserted_count + result.modified_count

    async def list_expected_sessions(
        self, from_season: int, to_season: int
    ) -> List[ExpectedSession]:
        cursor = self._db[EXPECTED_SESSIONS].find(
            {"season": {"$gte": from_season, "$lte": to_season}},
            projection={"_id": False},
        ).sort([("season", ASCENDING), ("round", ASCENDING)])
        return [ExpectedSession(**doc) async for doc in cursor]

    # ── Per-session state ────────────────────────────────────────────────────

    async def record_state(self, state: SessionIngestState) -> None:
        await self._db[SESSION_STATE].replace_one(
            {"season": state.season, "round": state.round},
            state.model_dump(mode="json"),
            upsert=True,
        )

    async def get_state(
        self, season: int, round_number: int
    ) -> Optional[SessionIngestState]:
        doc = await self._db[SESSION_STATE].find_one(
            {"season": season, "round": round_number}, projection={"_id": False}
        )
        return SessionIngestState(**doc) if doc else None

    async def list_states(
        self, from_season: int, to_season: int
    ) -> List[SessionIngestState]:
        cursor = self._db[SESSION_STATE].find(
            {"season": {"$gte": from_season, "$lte": to_season}},
            projection={"_id": False},
        ).sort([("season", ASCENDING), ("round", ASCENDING)])
        return [SessionIngestState(**doc) async for doc in cursor]

    # ── Session data ─────────────────────────────────────────────────────────

    async def save_payload(
        self, payload: SessionPayload, depth: IngestDepth = IngestDepth.FULL
    ) -> Dict[str, int]:
        """Persist one session's data, replacing any previous ingest of it.

        Returns the per-collection row counts actually written, which the caller
        records on the session state — so "how much landed" is answerable later
        without re-querying every collection.
        """
        run_id = str(uuid.uuid4())
        season = payload.session.season
        round_number = payload.session.round
        written: Dict[str, int] = {}

        for attribute, collection in DATA_COLLECTIONS.items():
            rows = getattr(payload, attribute)
            # A results-depth ingest fetched no laps, so its empty lap list means
            # "not requested", not "none exist". Clearing on that would let a
            # cheap training backfill silently destroy a full ingest's lap data.
            if depth is IngestDepth.RESULTS and attribute != "results" and not rows:
                continue
            written[attribute] = await self._replace_session_rows(
                collection, season, round_number, rows, run_id
            )
        return written

    async def _replace_session_rows(
        self,
        collection: str,
        season: int,
        round_number: int,
        rows: Iterable[Any],
        run_id: str,
    ) -> int:
        """Upsert this run's rows, then drop stragglers from earlier runs."""
        rows = list(rows)
        session_filter = {"season": season, "round": round_number}

        if not rows:
            # An empty dataset is a real outcome (no rain, no safety car) and
            # must clear whatever a previous run left behind.
            await self._db[collection].delete_many(session_filter)
            return 0

        operations: List[Any] = []
        for row in rows:
            document = row.model_dump(mode="json")
            document["_id"] = document.pop("id")
            document[RUN_FIELD] = run_id
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )

        # Same bulk_write as the upserts so the stale-row sweep cannot be skipped
        # by an early return or an exception between the two calls.
        operations.append(
            DeleteMany(dict(session_filter, **{RUN_FIELD: {"$ne": run_id}}))
        )
        await self._db[collection].bulk_write(operations, ordered=True)
        return len(rows)

    # ── Forward-looking data ─────────────────────────────────────────────────

    async def upsert_weekends(self, weekends: Sequence[Any]) -> int:
        if not weekends:
            return 0
        operations = [
            ReplaceOne(
                {"season": weekend.season, "round": weekend.round},
                weekend.model_dump(mode="json"),
                upsert=True,
            )
            for weekend in weekends
        ]
        result = await self._db[WEEKENDS].bulk_write(operations, ordered=False)
        return result.upserted_count + result.modified_count

    async def list_weekends(
        self, from_season: int, to_season: int
    ) -> List[Dict[str, Any]]:
        cursor = self._db[WEEKENDS].find(
            {"season": {"$gte": from_season, "$lte": to_season}},
            projection={"_id": False},
        ).sort([("season", ASCENDING), ("round", ASCENDING)])
        return [doc async for doc in cursor]

    async def save_qualifying(
        self, season: int, round_number: int, rows: Sequence[Any]
    ) -> int:
        """Replace the stored grid for one round.

        Unlike the race payload this never clears on empty: a failed or
        not-yet-run qualifying session must not delete a grid we already have.
        """
        if not rows:
            return 0

        run_id = str(uuid.uuid4())
        operations: List[Any] = []
        for row in rows:
            document = row.model_dump(mode="json")
            document["_id"] = document.pop("id")
            document[RUN_FIELD] = run_id
            document["_ingested_at"] = datetime.now(timezone.utc)
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )
        operations.append(
            DeleteMany(
                {
                    "season": season,
                    "round": round_number,
                    RUN_FIELD: {"$ne": run_id},
                }
            )
        )
        await self._db[QUALIFYING].bulk_write(operations, ordered=True)
        return len(rows)

    async def save_signals(
        self, season: int, round_number: int, rows: Sequence[Any]
    ) -> int:
        """Replace extracted signals for one round.

        Clears on empty, unlike qualifying: re-extracting a round with better
        rules should remove signals the old rules produced, and an empty result
        is a legitimate outcome for an uneventful race.
        """
        session_filter = {"season": season, "round": round_number}
        if not rows:
            await self._db[SIGNALS].delete_many(session_filter)
            return 0

        run_id = str(uuid.uuid4())
        operations: List[Any] = []
        for row in rows:
            document = row.model_dump(mode="json")
            document["_id"] = document.pop("id")
            document[RUN_FIELD] = run_id
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )
        operations.append(
            DeleteMany(dict(session_filter, **{RUN_FIELD: {"$ne": run_id}}))
        )
        await self._db[SIGNALS].bulk_write(operations, ordered=True)
        return len(rows)

    async def save_practice(
        self, season: int, round_number: int, session_name: str, rows: Sequence[Any]
    ) -> int:
        """Replace stored pace for one practice session.

        Scoped to the session, not the weekend: FP1 landing must not clear FP2.
        Never clears on empty, for the same reason qualifying does not — a
        failed fetch must not delete a session we already had.
        """
        if not rows:
            return 0

        run_id = str(uuid.uuid4())
        operations: List[Any] = []
        for row in rows:
            document = row.model_dump(mode="json")
            document["_id"] = document.pop("id")
            document[RUN_FIELD] = run_id
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )
        operations.append(
            DeleteMany({
                "season": season,
                "round": round_number,
                "session_name": session_name,
                RUN_FIELD: {"$ne": run_id},
            })
        )
        await self._db[PRACTICE].bulk_write(operations, ordered=True)
        return len(rows)

    async def qualifying_presence(
        self, season: int, round_number: int
    ) -> Tuple[int, Optional[datetime]]:
        """How many grid rows exist for a round, and when they landed."""
        count = await self._db[QUALIFYING].count_documents(
            {"season": season, "round": round_number}
        )
        if not count:
            return 0, None
        newest = await self._db[QUALIFYING].find_one(
            {"season": season, "round": round_number},
            projection={"_ingested_at": True},
            sort=[("_ingested_at", DESCENDING)],
        )
        return count, (newest or {}).get("_ingested_at")

    # ── Read paths ───────────────────────────────────────────────────────────

    async def find_rows(
        self,
        collection: str,
        query: Optional[Dict[str, Any]] = None,
        limit: int = 0,
        sort: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generic read used by the query routers.

        Excludes the run-tag bookkeeping field so callers never see it.
        """
        cursor = self._db[collection].find(
            query or {}, projection={RUN_FIELD: False}
        )
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [_normalise_id(doc) async for doc in cursor]

    async def count_matching(self, collection: str, query: Dict[str, Any]) -> int:
        """How many rows match. Used for cheap existence checks."""
        return await self._db[collection].count_documents(query)

    async def _circuit_names(self, circuit: str) -> List[str]:
        """Every stored spelling of this circuit.

        Queries by circuit name have to go through this. The stored rows keep
        whatever name upstream used at the time — "Abu Dhabi", then "Yas
        Marina", then "Yas Island" — so matching on one of them finds a
        fraction of the races and reports it as the circuit's whole history.
        Resolved against the database rather than a list, so a spelling nobody
        anticipated still lands in the right place.
        """
        from app.services.circuit_map import slug

        wanted = slug(circuit)
        stored = await self._db[DATA_COLLECTIONS["results"]].distinct("circuit")
        names = [name for name in stored if slug(name) == wanted]
        return names or [circuit]

    async def circuit_visits(self, circuit: str) -> List[Tuple[int, str]]:
        """Every (season, event name) we hold at a circuit, newest first.

        Newest first because a map derived from last year is as good as one
        from this year, and the current weekend is the visit least likely to
        have telemetry published yet.
        """
        names = await self._circuit_names(circuit)
        rows = await self._db[DATA_COLLECTIONS["results"]].distinct(
            "season", {"circuit": {"$in": names}}
        )
        visits: List[Tuple[int, str]] = []
        for season in sorted(rows, reverse=True):
            name = await self._db[DATA_COLLECTIONS["results"]].find_one(
                {"circuit": {"$in": names}, "season": season},
                {"_id": 0, "race_name": 1},
            )
            if name and name.get("race_name"):
                visits.append((int(season), name["race_name"]))
        return visits

    async def circuit_history(self, circuit: str) -> Dict[str, Any]:
        """Every race and lap we hold at a circuit, for the stats summary.

        Laps are matched by race name rather than circuit because the lap rows
        do not carry one — the race names come from the results, so the two
        sets cannot disagree about which events count.
        """
        names = await self._circuit_names(circuit)
        results = [
            row async for row in
            self._db[DATA_COLLECTIONS["results"]].find(
                {"circuit": {"$in": names}}, {"_id": 0}
            )
        ]
        names = sorted({row["race_name"] for row in results if row.get("race_name")})
        laps: List[Dict[str, Any]] = []
        if names:
            laps = [
                row async for row in self._db[DATA_COLLECTIONS["laps"]].find(
                    {"race_name": {"$in": names}},
                    {"_id": 0, "lap_time_seconds": 1, "speed_trap_kph": 1,
                     "driver": 1, "season": 1},
                )
            ]
        return {"results": results, "laps": laps}

    async def save_circuit_map(self, document: Dict[str, Any]) -> None:
        """Store a circuit's geometry, replacing any earlier derivation.

        Keyed on the circuit rather than the weekend. Twenty seasons of Monza
        are one shape, and deriving it per visit would pay the telemetry cost
        every year to redraw the same track.
        """
        await self._db[CIRCUITS].replace_one(
            {"id": document["id"]}, document, upsert=True
        )

    async def circuit_map(self, circuit: str) -> Optional[Dict[str, Any]]:
        """A circuit's stored geometry, or None if it was never derived."""
        from app.services.circuit_map import slug

        return await self._db[CIRCUITS].find_one(
            {"id": slug(circuit)}, {"_id": 0}
        )

    async def circuits_with_maps(self) -> List[str]:
        """Slugs of every circuit that has one. Used to skip derivation."""
        return await self._db[CIRCUITS].distinct("id")

    async def remember_drivers(self, season: int, identities: Dict[int, Any]) -> int:
        """Record who drives which car this season.

        Called on every successful entry-list read, so the registry stays warm
        without a separate job. Upserts rather than replaces the season: a
        mid-season driver change should add an entry, not erase the drivers who
        have not changed.
        """
        if not identities:
            return 0
        operations = [
            UpdateOne(
                {"season": season, "driver_number": int(number)},
                {"$set": {
                    "season": season,
                    "driver_number": int(number),
                    "driver": driver,
                    "team": team,
                    "last_seen": datetime.now(timezone.utc),
                }},
                upsert=True,
            )
            for number, (driver, team) in identities.items()
            if int(number) > 0 and driver
        ]
        if not operations:
            return 0
        result = await self._db[DRIVER_REGISTRY].bulk_write(operations, ordered=False)
        return (result.upserted_count or 0) + (result.modified_count or 0)

    async def recall_drivers(self, season: int) -> Dict[int, Any]:
        """The stored entry list for a season, as ``{car_number: (driver, team)}``.

        Only ever consulted when the live one cannot be read. It is a cache of a
        fact, not a source of truth: if FastF1 is answering, its answer wins,
        because a driver swap we have not yet seen would otherwise be served
        from memory.
        """
        cursor = self._db[DRIVER_REGISTRY].find(
            {"season": season}, projection={"_id": False}
        )
        return {
            int(doc["driver_number"]): (doc["driver"], doc.get("team", "Unknown"))
            async for doc in cursor
        }

    async def distinct_seasons(self) -> List[int]:
        seasons = await self._db[DATA_COLLECTIONS["results"]].distinct("season")
        return sorted(int(season) for season in seasons)

    async def count_rows(self, collection: str) -> int:
        return await self._db[collection].count_documents({})


def _normalise_id(document: Dict[str, Any]) -> Dict[str, Any]:
    """Present the stable id back under its domain name."""
    if "_id" in document:
        document["id"] = str(document.pop("_id"))
    return document
