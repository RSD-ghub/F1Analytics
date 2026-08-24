"""Scoring and track-record endpoints.

``/track-record`` is the product's central honesty claim rendered as JSON. It is
deliberately read-only and derived on request rather than cached: a stale
accuracy record is worse than a slow one, because it is indistinguishable from
an accurate one.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_reconciler, get_store
from app.models.schemas import (
    Market,
    PredictionScore,
    RaceOutcome,
    TrackRecord,
)
from app.services.clients import UpstreamUnavailable
from app.services.reconciler import Reconciler
from app.services.storage import ScoringStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scoring"])


class ReconcileResponse(BaseModel):
    season: int
    round: int
    reconciled: bool
    scored: int = 0
    message: str = ""


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, UpstreamUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


# ── Reconciliation ───────────────────────────────────────────────────────────


@router.post("/reconcile/{season}/{round_number}", response_model=ReconcileResponse)
async def reconcile_race(
    season: int,
    round_number: int,
    reconciler: Reconciler = Depends(get_reconciler),
) -> ReconcileResponse:
    """Score every locked prediction for one race.

    Idempotent. A race with no usable result yet returns ``reconciled: false``
    with a 200 — an upcoming race is a normal state, not an error.
    """
    try:
        outcome, scores = await reconciler.reconcile_race(season, round_number)
    except Exception as exc:
        raise _guard(exc)

    if outcome is None:
        return ReconcileResponse(
            season=season,
            round=round_number,
            reconciled=False,
            message="no classified winner ingested yet; nothing to score",
        )
    return ReconcileResponse(
        season=season, round=round_number, reconciled=True, scored=len(scores)
    )


@router.post("/reconcile/{season}", response_model=List[PredictionScore])
async def reconcile_season(
    season: int,
    reconciler: Reconciler = Depends(get_reconciler),
) -> List[PredictionScore]:
    """Sweep a whole season. Safe to re-run."""
    try:
        return await reconciler.reconcile_season(season)
    except Exception as exc:
        raise _guard(exc)


# ── Reading ──────────────────────────────────────────────────────────────────


@router.get("/track-record", response_model=TrackRecord)
async def track_record(
    season: Optional[int] = Query(None),
    settings: Settings = Depends(get_settings),
    reconciler: Reconciler = Depends(get_reconciler),
) -> TrackRecord:
    """The public accuracy record.

    Includes ``predictions_pending`` on purpose: a record showing only scored
    predictions could be improved by never reconciling the bad ones.
    """
    try:
        return await reconciler.track_record(
            season=season, buckets=settings.calibration_buckets
        )
    except Exception as exc:
        raise _guard(exc)


@router.get("/scores", response_model=List[PredictionScore])
async def list_scores(
    season: Optional[int] = Query(None),
    window: Optional[str] = Query(None),
    model_version: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    store: ScoringStore = Depends(get_store),
) -> List[PredictionScore]:
    return await store.list_scores(season, window, model_version, limit)


@router.get("/scores/{prediction_id}", response_model=PredictionScore)
async def get_score(
    prediction_id: str,
    store: ScoringStore = Depends(get_store),
) -> PredictionScore:
    score = await store.get_score(prediction_id)
    if score is None:
        raise HTTPException(
            status_code=404, detail="no score for prediction {}".format(prediction_id)
        )
    return score


@router.get("/outcomes/{season}/{round_number}", response_model=RaceOutcome)
async def get_outcome(
    season: int,
    round_number: int,
    store: ScoringStore = Depends(get_store),
) -> RaceOutcome:
    outcome = await store.get_outcome(season, round_number)
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail="{}-{} has not been reconciled".format(season, round_number),
        )
    return outcome
