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

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.models.schemas import PredictionScore, RaceOutcome

logger = logging.getLogger(__name__)

OUTCOMES = "outcomes"
SCORES = "scores"


class ScoringStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        await self._db[OUTCOMES].create_index(
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

    async def save_outcome(self, outcome: RaceOutcome) -> None:
        await self._db[OUTCOMES].replace_one(
            {"season": outcome.season, "round": outcome.round},
            outcome.model_dump(mode="json"),
            upsert=True,
        )

    async def get_outcome(
        self, season: int, round_number: int
    ) -> Optional[RaceOutcome]:
        doc = await self._db[OUTCOMES].find_one(
            {"season": season, "round": round_number}, projection={"_id": False}
        )
        return RaceOutcome(**doc) if doc else None

    async def list_outcomes(self, season: Optional[int] = None) -> List[RaceOutcome]:
        query = {"season": season} if season is not None else {}
        cursor = self._db[OUTCOMES].find(query, projection={"_id": False}).sort(
            [("season", ASCENDING), ("round", ASCENDING)]
        )
        return [RaceOutcome(**doc) async for doc in cursor]

    # ── Scores ───────────────────────────────────────────────────────────────

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
