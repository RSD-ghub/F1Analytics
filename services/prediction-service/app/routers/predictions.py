"""Prediction endpoints.

``/preview`` and ``/lock`` deliberately do the same computation and differ only
in whether the result is permanent. That split matters: previewing must be free
and repeatable during development, while locking is a one-way door that writes to
the public record.

A second lock attempt returns 409, not 200. Silently returning the existing
prediction would let a caller believe it had re-locked with fresh data.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_client, get_model, get_predictor, get_store
from app.models.schemas import (
    ChampionshipForecast,
    FeatureSnapshot,
    LockWindow,
    ModelVersion,
    Prediction,
)
from app.services import simulation
from app.services.features import build_snapshot
from app.services.ingestion_client import IngestionClient, IngestionUnavailable
from app.services.model import RaceModel
from app.services.predictor import (
    ConfirmedGridRequired,
    GridRequired,
    Predictor,
)
from app.services.storage import PredictionExists, PredictionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])


class ForecastRequest(BaseModel):
    season: int
    round: int
    window: LockWindow = LockWindow.PRE_QUALI
    circuit: str = ""
    race_name: str = ""


class PreviewResponse(BaseModel):
    prediction: Prediction
    snapshot: FeatureSnapshot


def _handle(exc: Exception) -> HTTPException:
    """Map domain errors to the status codes that describe them honestly."""
    if isinstance(exc, PredictionExists):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConfirmedGridRequired):
        # 409, not 500: the caller asked for a forecast on the confirmed grid
        # and the FIA has not published it yet. An expected state in the window
        # between qualifying and T-1h, and the caller's move is to wait.
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, GridRequired):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, IngestionUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.post("/preview", response_model=PreviewResponse)
async def preview(
    request: ForecastRequest,
    predictor: Predictor = Depends(get_predictor),
) -> PreviewResponse:
    """Build a forecast without recording it.

    Returns the feature snapshot alongside, so the inputs behind any number are
    inspectable rather than having to be inferred.
    """
    try:
        prediction, snapshot = await predictor.build(
            season=request.season,
            round_number=request.round,
            window=request.window,
            circuit=request.circuit,
            race_name=request.race_name,
        )
    except Exception as exc:
        raise _handle(exc)
    return PreviewResponse(prediction=prediction, snapshot=snapshot)


@router.post("/lock", response_model=Prediction, status_code=201)
async def lock(
    request: ForecastRequest,
    predictor: Predictor = Depends(get_predictor),
) -> Prediction:
    """Permanently record a forecast. One-way — 409 if already locked."""
    try:
        return await predictor.lock(
            season=request.season,
            round_number=request.round,
            window=request.window,
            circuit=request.circuit,
            race_name=request.race_name,
        )
    except Exception as exc:
        raise _handle(exc)


@router.get("", response_model=List[Prediction])
async def list_predictions(
    season: Optional[int] = Query(None),
    round_number: Optional[int] = Query(None, alias="round"),
    limit: int = Query(100, ge=1, le=500),
    store: PredictionStore = Depends(get_store),
) -> List[Prediction]:
    return await store.list_predictions(season, round_number, limit)


# Literal paths must be registered before /{season}/{round_number}: FastAPI
# matches in registration order, so declaring the parameterised route first
# would make /predictions/model/versions try to parse "model" as a season and
# fail with a 422 instead of reaching this handler.
@router.get("/snapshot/{snapshot_id}", response_model=FeatureSnapshot)
async def snapshot(
    snapshot_id: str,
    store: PredictionStore = Depends(get_store),
) -> FeatureSnapshot:
    """The exact features behind a prediction — the reproducibility handle."""
    found = await store.get_snapshot(snapshot_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no snapshot {}".format(snapshot_id))
    return found


@router.get("/model/versions", response_model=List[ModelVersion])
async def model_versions(
    store: PredictionStore = Depends(get_store),
) -> List[ModelVersion]:
    return await store.list_model_versions()


@router.get("/{season}/{round_number}", response_model=List[Prediction])
async def for_race(
    season: int,
    round_number: int,
    store: PredictionStore = Depends(get_store),
) -> List[Prediction]:
    """Both windows for one race, so their differences can be compared directly."""
    return await store.list_predictions(season, round_number)


# ── Championship ─────────────────────────────────────────────────────────────

championship_router = APIRouter(prefix="/championship", tags=["championship"])


@championship_router.get("/{season}", response_model=ChampionshipForecast)
async def championship(
    season: int,
    runs: Optional[int] = Query(None, ge=100, le=100000),
    seed: int = Query(12345),
    settings: Settings = Depends(get_settings),
    client: IngestionClient = Depends(get_client),
    model: RaceModel = Depends(get_model),
    store: PredictionStore = Depends(get_store),
) -> ChampionshipForecast:
    """Title probabilities from simulating every remaining race.

    Assumes the season continues as it looks today: form is frozen at current
    values and races are simulated independently, so neither a mid-season
    upgrade nor a correlated run of failures is anticipated.
    """
    try:
        results = await client.season_results(season)
        weekends = await client.weekends(season)
    except IngestionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    completed = sorted({row.round for row in results if row.season == season})
    as_of = max(completed) if completed else 0
    remaining = sorted(
        weekend.round
        for weekend in weekends
        if weekend.season == season and weekend.round > as_of
    )

    # Features are built as-of the next unrun round, so the simulation starts
    # from the same information a forecast for that race would have.
    snapshot = build_snapshot(
        results,
        season=season,
        target_round=as_of + 1,
        window=LockWindow.PRE_QUALI,
    )

    forecast = simulation.simulate_championship(
        model=model,
        drivers=snapshot.drivers,
        current_points=simulation.current_points_from(results, season),
        remaining_rounds=remaining,
        season=season,
        as_of_round=as_of,
        runs=runs or settings.simulation_runs,
        seed=seed,
    )
    await store.save_championship(forecast)
    return forecast
