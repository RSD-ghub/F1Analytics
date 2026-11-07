"""Mongo persistence for outcomes and scores.

Scores are keyed on ``prediction_id`` and replaced on re-scoring rather than
appended. That is safe in a way editing a prediction is not: a score is a pure
function of a locked prediction and an immutable race result, so re-running it
either reproduces the same number or reveals a scoring bug. The prediction —
the thing someone could be tempted to improve after the fact — remains
untouchable upstream.
"""

import logging
from typing import Any, Dict, List, Optional

from f1_common.cache import invalidates
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.models.schemas import RACE, SPRINT, PredictionScore, RaceOutcome

logger = logging.getLogger(__name__)

OUTCOMES = "outcomes"
#: Sprint outcomes live apart from the Grand Prix's: ``outcomes`` is unique on
#: (season, round), and a sprint shares its round with a Grand Prix.
SPRINT_OUTCOMES = "sprint_outcomes"
SCORES = "scores"
#: One document per alert already delivered, so each problem is sent once.
ALERTS = "alerts_sent"


class ScoringStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        for collection in (OUTCOMES, SPRINT_OUTCOMES):
            await self._db[collection].create_index(
                [("season", ASCENDING), ("round", ASCENDING)], unique=True
            )
        await self._db[SCORES].create_index(
            [("prediction_id", ASCENDING)], unique=True
        )
        await self._db[SCORES].create_index(
            [("season", ASCENDING), ("round", ASCENDING), ("window", ASCENDING)]
        )
        await self._db[SCORES].create_index([("model_version", ASCENDING)])

    # ── Outcomes ─────────────────────────────────────────────────────────────

    @invalidates("scoring")
    async def save_outcome(self, outcome: RaceOutcome) -> None:
        await self._db[_outcomes_for(outcome.event)].replace_one(
            {"season": outcome.season, "round": outcome.round},
            outcome.model_dump(mode="json"),
            upsert=True,
        )

    async def get_outcome(
        self, season: int, round_number: int, event: str = RACE
    ) -> Optional[RaceOutcome]:
        doc = await self._db[_outcomes_for(event)].find_one(
            {"season": season, "round": round_number}, projection={"_id": False}
        )
        return RaceOutcome(**doc) if doc else None

    async def list_outcomes(self, season: Optional[int] = None) -> List[RaceOutcome]:
        """Grand Prix and sprint outcomes together, each under its own key."""
        query = {"season": season} if season is not None else {}
        found: List[RaceOutcome] = []
        for collection in (OUTCOMES, SPRINT_OUTCOMES):
            cursor = self._db[collection].find(
                query, projection={"_id": False}
            ).sort([("season", ASCENDING), ("round", ASCENDING)])
            found.extend([RaceOutcome(**doc) async for doc in cursor])
        return found

    # ── Scores ───────────────────────────────────────────────────────────────

    @invalidates("scoring")
    async def alert_sent(self, key: str) -> bool:
        return await self._db[ALERTS].find_one({"key": key}) is not None

    async def mark_alert_sent(self, key: str, message: str, sent_at) -> None:
        await self._db[ALERTS].replace_one(
            {"key": key},
            {"key": key, "message": message, "sent_at": sent_at},
            upsert=True,
        )

    async def save_score(self, score: PredictionScore) -> None:
        await self._db[SCORES].replace_one(
            {"prediction_id": score.prediction_id},
            score.model_dump(mode="json"),
            upsert=True,
        )

    async def get_score(self, prediction_id: str) -> Optional[PredictionScore]:
        doc = await self._db[SCORES].find_one(
            {"prediction_id": prediction_id}, projection={"_id": False}
        )
        return PredictionScore(**doc) if doc else None

    async def list_scores(
        self,
        season: Optional[int] = None,
        window: Optional[str] = None,
        model_version: Optional[str] = None,
        limit: int = 1000,
    ) -> List[PredictionScore]:
        query: Dict[str, Any] = {}
        if season is not None:
            query["season"] = season
        if window:
            query["window"] = window
        if model_version:
            query["model_version"] = model_version

        cursor = (
            self._db[SCORES]
            .find(query, projection={"_id": False})
            .sort([("season", DESCENDING), ("round", DESCENDING)])
            .limit(limit)
        )
        return [PredictionScore(**doc) async for doc in cursor]

    async def scored_prediction_ids(self) -> List[str]:
        return await self._db[SCORES].distinct("prediction_id")


def _outcomes_for(event: str) -> str:
    return SPRINT_OUTCOMES if event == SPRINT else OUTCOMES
