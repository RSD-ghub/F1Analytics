"""Mongo persistence for predictions.

**This class has no update method for predictions, and that is the design.** A
locked forecast is a historical fact; the ability to edit one is the ability to
quietly improve the track record after seeing the result. Insert and read are the
only operations exposed, and a unique index on (season, round, window) makes a
second lock for the same window fail loudly at the database rather than
overwriting the first.

Feature snapshots are content-addressed by hash, so re-locking with identical
features reuses the existing document instead of duplicating it.
"""

import logging
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.models.schemas import (
    ChampionshipForecast,
    FeatureSnapshot,
    LockWindow,
    ModelVersion,
    Prediction,
)

logger = logging.getLogger(__name__)

PREDICTIONS = "predictions"
FEATURE_SNAPSHOTS = "feature_snapshots"
MODEL_VERSIONS = "model_versions"
CHAMPIONSHIP = "championship_forecasts"


class PredictionExists(RuntimeError):
    """A prediction is already locked for this season/round/window.

    Not an error condition to route around — it is the immutability guarantee
    doing its job.
    """


class PredictionStore:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    async def ensure_indexes(self) -> None:
        # The immutability enforcement point. A duplicate lock hits this and
        # raises rather than replacing a published prediction.
        await self._db[PREDICTIONS].create_index(
            [("season", ASCENDING), ("round", ASCENDING), ("window", ASCENDING)],
            unique=True,
        )
        await self._db[PREDICTIONS].create_index([("locked_at", DESCENDING)])
        await self._db[PREDICTIONS].create_index([("model_version", ASCENDING)])
        await self._db[FEATURE_SNAPSHOTS].create_index(
            [("snapshot_id", ASCENDING)], unique=True
        )
        await self._db[MODEL_VERSIONS].create_index(
            [("version", ASCENDING)], unique=True
        )
        await self._db[CHAMPIONSHIP].create_index(
            [("season", ASCENDING), ("as_of_round", DESCENDING)]
        )

    # ── Predictions (append-only) ────────────────────────────────────────────

    async def insert_prediction(self, prediction: Prediction) -> Prediction:
        """Write a locked prediction. Never overwrites."""
        try:
            await self._db[PREDICTIONS].insert_one(
                dict(prediction.model_dump(mode="json"), _id=prediction.prediction_id)
            )
        except DuplicateKeyError as exc:
            raise PredictionExists(
                "a {} prediction is already locked for {}-{}".format(
                    prediction.window.value, prediction.season, prediction.round
                )
            ) from exc
        return prediction

    async def get_prediction(
        self, season: int, round_number: int, window: LockWindow
    ) -> Optional[Prediction]:
        doc = await self._db[PREDICTIONS].find_one(
            {"season": season, "round": round_number, "window": window.value},
            projection={"_id": False},
        )
        return Prediction(**doc) if doc else None

    async def list_predictions(
        self,
        season: Optional[int] = None,
        round_number: Optional[int] = None,
        limit: int = 200,
    ) -> List[Prediction]:
        query: Dict[str, Any] = {}
        if season is not None:
            query["season"] = season
        if round_number is not None:
            query["round"] = round_number

        cursor = (
            self._db[PREDICTIONS]
            .find(query, projection={"_id": False})
            .sort([("season", DESCENDING), ("round", DESCENDING)])
            .limit(limit)
        )
        return [Prediction(**doc) async for doc in cursor]

    # ── Feature snapshots ────────────────────────────────────────────────────

    async def save_snapshot(self, snapshot: FeatureSnapshot) -> str:
        """Store a snapshot idempotently; identical features reuse the document."""
        await self._db[FEATURE_SNAPSHOTS].update_one(
            {"snapshot_id": snapshot.snapshot_id},
            {"$setOnInsert": snapshot.model_dump(mode="json")},
            upsert=True,
        )
        return snapshot.snapshot_id

    async def get_snapshot(self, snapshot_id: str) -> Optional[FeatureSnapshot]:
        doc = await self._db[FEATURE_SNAPSHOTS].find_one(
            {"snapshot_id": snapshot_id}, projection={"_id": False}
        )
        return FeatureSnapshot(**doc) if doc else None

    # ── Model versions ───────────────────────────────────────────────────────

    async def record_model_version(self, version: ModelVersion) -> None:
        await self._db[MODEL_VERSIONS].update_one(
            {"version": version.version},
            {"$setOnInsert": version.model_dump(mode="json")},
            upsert=True,
        )

    async def list_model_versions(self) -> List[ModelVersion]:
        cursor = self._db[MODEL_VERSIONS].find(
            {}, projection={"_id": False}
        ).sort([("created_at", DESCENDING)])
        return [ModelVersion(**doc) async for doc in cursor]

    # ── Championship forecasts ───────────────────────────────────────────────

    async def save_championship(self, forecast: ChampionshipForecast) -> None:
        """Latest forecast per (season, as_of_round).

        Overwritable, unlike a prediction: this is a derived view of the current
        state, not a locked call anyone is scored on.
        """
        await self._db[CHAMPIONSHIP].replace_one(
            {"season": forecast.season, "as_of_round": forecast.as_of_round},
            forecast.model_dump(mode="json"),
            upsert=True,
        )

    async def latest_championship(
        self, season: int
    ) -> Optional[ChampionshipForecast]:
        doc = await self._db[CHAMPIONSHIP].find_one(
            {"season": season},
            projection={"_id": False},
            sort=[("as_of_round", DESCENDING)],
        )
        return ChampionshipForecast(**doc) if doc else None
