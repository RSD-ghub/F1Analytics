"""Ingest control and completeness reporting.

``/ingest/status`` is the endpoint that makes the guarantee real: it answers "is
the dataset whole?" with a number and a list of specific missing sessions, rather
than requiring someone to read logs. Prediction lock windows will gate on it in
Phase 4.

Backfills run as background tasks and are polled via ``/ingest/status`` — a
2010-onwards run takes hours, far past any sensible HTTP timeout.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_runner, get_store, season_range
from app.models.schemas import (
    CompletenessSummary,
    IngestDepth,
    SessionIngestState,
)
from app.services.completeness import GAP_STATES
from app.services.ingest_runner import IngestRunner
from app.services.storage import IngestionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


class BackfillRequest(BaseModel):
    from_season: Optional[int] = None
    to_season: Optional[int] = None
    #: Re-ingest only sessions that are missing, partial or failed. This is the
    #: healing pass and the sane default for a scheduled run.
    only_gaps: bool = True
    #: ``results`` fetches classification + qualifying only (~2s/session) — the
    #: corpus the model trains on. ``full`` also reconstructs laps, stints, pit
    #: stops, weather, race control and telemetry (~30s/session).
    depth: IngestDepth = IngestDepth.FULL


class BackfillAccepted(BaseModel):
    accepted: bool = True
    from_season: int
    to_season: int
    only_gaps: bool
    depth: IngestDepth = IngestDepth.FULL
    message: str


class StatusResponse(BaseModel):
    summary: CompletenessSummary
    is_complete: bool


def _resolve_span(
    settings: Settings, from_season: Optional[int], to_season: Optional[int]
):
    default_from, default_to = season_range(settings)
    start = from_season if from_season is not None else default_from
    end = to_season if to_season is not None else default_to
    if start > end:
        start, end = end, start
    if start < settings.season_floor:
        raise HTTPException(
            status_code=400,
            detail="from_season {} predates the configured floor {}".format(
                start, settings.season_floor
            ),
        )
    return start, end


@router.get("/status", response_model=StatusResponse)
async def ingest_status(
    from_season: Optional[int] = Query(None),
    to_season: Optional[int] = Query(None),
    depth: IngestDepth = Query(IngestDepth.FULL),
    settings: Settings = Depends(get_settings),
    runner: IngestRunner = Depends(get_runner),
) -> StatusResponse:
    """Is the dataset whole, and if not, exactly which sessions are missing?

    ``depth`` decides what "whole" means. A corpus complete for model training
    (results + qualifying) is not complete for lap-level analytics, and one
    number cannot honestly answer both.
    """
    start, end = _resolve_span(settings, from_season, to_season)
    summary = await runner.status(start, end, depth=depth)
    return StatusResponse(summary=summary, is_complete=summary.is_complete)


@router.get("/gaps", response_model=List[SessionIngestState])
async def open_gaps(
    from_season: Optional[int] = Query(None),
    to_season: Optional[int] = Query(None),
    settings: Settings = Depends(get_settings),
    store: IngestionStore = Depends(get_store),
) -> List[SessionIngestState]:
    """Full state records for sessions that are not whole, with failure reasons."""
    start, end = _resolve_span(settings, from_season, to_season)
    states = await store.list_states(start, end)
    return [state for state in states if state.state in GAP_STATES]


@router.post("/backfill", response_model=BackfillAccepted, status_code=202)
async def start_backfill(
    request: BackfillRequest,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    runner: IngestRunner = Depends(get_runner),
) -> BackfillAccepted:
    """Kick off a backfill in the background; poll ``/ingest/status`` for progress."""
    start, end = _resolve_span(settings, request.from_season, request.to_season)
    background.add_task(
        _run_backfill, runner, start, end, request.only_gaps, request.depth
    )
    return BackfillAccepted(
        from_season=start,
        to_season=end,
        only_gaps=request.only_gaps,
        depth=request.depth,
        message=(
            "Backfill started at {} depth. Poll /ingest/status?depth={} for "
            "completeness.".format(request.depth.value, request.depth.value)
        ),
    )


@router.post("/season/{season}", response_model=BackfillAccepted, status_code=202)
async def ingest_season(
    season: int,
    background: BackgroundTasks,
    only_gaps: bool = Query(False),
    settings: Settings = Depends(get_settings),
    runner: IngestRunner = Depends(get_runner),
) -> BackfillAccepted:
    start, end = _resolve_span(settings, season, season)
    background.add_task(_run_backfill, runner, start, end, only_gaps)
    return BackfillAccepted(
        from_season=start,
        to_season=end,
        only_gaps=only_gaps,
        message="Season {} ingest started.".format(season),
    )


@router.get("/state/{season}/{round_number}", response_model=SessionIngestState)
async def session_state(
    season: int,
    round_number: int,
    store: IngestionStore = Depends(get_store),
) -> SessionIngestState:
    state = await store.get_state(season, round_number)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="no ingest state for {}-{}; run the manifest refresh first".format(
                season, round_number
            ),
        )
    return state


async def _run_backfill(
    runner: IngestRunner,
    from_season: int,
    to_season: int,
    only_gaps: bool,
    depth: IngestDepth = IngestDepth.FULL,
) -> None:
    """Background entry point.

    Exceptions are logged rather than raised: a background task that dies
    silently would leave the caller polling a status that never changes. The
    per-session states written along the way remain the durable record.
    """
    try:
        await runner.run_backfill(
            from_season, to_season, only_gaps=only_gaps, depth=depth
        )
    except Exception:
        logger.exception("backfill %s-%s aborted", from_season, to_season)
