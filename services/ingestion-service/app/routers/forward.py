"""Forward-looking endpoints: upcoming races, grid freshness, standings.

Consumed by prediction-service to decide when to fire a lock window and what to
condition on, and by core-api to render the next-race view.

The standings endpoints expose the point-in-time distinction directly in the URL
(``/standings/{season}`` vs ``/standings/{season}/before/{round}``) rather than
hiding it behind a flag. A leaky call should be visible in an access log.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.dependencies import get_runner, get_store, season_range
from app.models.schemas import (
    ExpectedSession,
    QualifyingFreshness,
    RaceWeekend,
    ResultRow,
)
from app.services import upcoming
from app.services import fia_documents, grid_resolution
from app.services.ingest_runner import IngestRunner
from app.services.standings import (
    StandingsSnapshot,
    compute_standings,
    standings_before_round,
)
from app.services.storage import DATA_COLLECTIONS, IngestionStore

router = APIRouter(prefix="/forward", tags=["forward"])


async def _weekends(
    store: IngestionStore, from_season: int, to_season: int
) -> List[RaceWeekend]:
    return [
        RaceWeekend(**doc) for doc in await store.list_weekends(from_season, to_season)
    ]


async def _season_results(store: IngestionStore, season: int) -> List[ResultRow]:
    rows = await store.find_rows(
        DATA_COLLECTIONS["results"], {"season": season}, limit=0
    )
    return [ResultRow(**row) for row in rows]


@router.get("/next", response_model=Optional[RaceWeekend])
async def next_race(
    at: Optional[datetime] = Query(None, description="Override 'now' (testing)"),
    settings: Settings = Depends(get_settings),
    store: IngestionStore = Depends(get_store),
):
    """The next race that has not started, or null if the calendar is exhausted."""
    _, current = _span(settings)
    # Look into next season too: in December the next race is in January.
    weekends = await _weekends(store, current, current + 1)
    return upcoming.next_race(weekends, at=at)


@router.get("/upcoming", response_model=List[RaceWeekend])
async def upcoming_races(
    limit: int = Query(5, ge=1, le=25),
    at: Optional[datetime] = Query(None),
    settings: Settings = Depends(get_settings),
    store: IngestionStore = Depends(get_store),
) -> List[RaceWeekend]:
    _, current = _span(settings)
    weekends = await _weekends(store, current, current + 1)
    return upcoming.upcoming_races(weekends, at=at, limit=limit)


@router.get(
    "/qualifying-freshness/{season}/{round_number}",
    response_model=QualifyingFreshness,
)
async def qualifying_freshness(
    season: int,
    round_number: int,
    at: Optional[datetime] = Query(None),
    store: IngestionStore = Depends(get_store),
) -> QualifyingFreshness:
    """Has qualifying run, and has the grid actually landed?

    ``is_stale`` true means qualifying is over but we have no grid — the state a
    post-quali lock window must refuse to fire on.
    """
    weekends = await store.list_weekends(season, season)
    match = next((w for w in weekends if w["round"] == round_number), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="no timetable for {}-{}; refresh the calendar first".format(
                season, round_number
            ),
        )

    count, ingested_at = await store.qualifying_presence(season, round_number)
    return upcoming.qualifying_freshness(
        RaceWeekend(**match), driver_count=count, ingested_at=ingested_at, at=at
    )


@router.get("/standings/{season}", response_model=StandingsSnapshot)
async def current_standings(
    season: int,
    store: IngestionStore = Depends(get_store),
) -> StandingsSnapshot:
    """Standings including every ingested round. Display only — not for forecasts."""
    return compute_standings(await _season_results(store, season), season)


@router.get("/standings/{season}/before/{round_number}", response_model=StandingsSnapshot)
async def standings_before(
    season: int,
    round_number: int,
    store: IngestionStore = Depends(get_store),
) -> StandingsSnapshot:
    """Standings as they stood going into ``round_number``.

    The leakage-safe variant, and the only one a forecast for that round may use.
    The returned ``included_rounds`` makes the claim auditable.
    """
    return standings_before_round(
        await _season_results(store, season), season, round_number
    )


@router.post("/refresh-calendar", status_code=202)
async def refresh_calendar(
    from_season: Optional[int] = Query(None),
    to_season: Optional[int] = Query(None),
    settings: Settings = Depends(get_settings),
    runner: IngestRunner = Depends(get_runner),
):
    """Re-fetch session timetables. Cheap; safe to call often."""
    floor, current = _span(settings)
    start = from_season if from_season is not None else current
    end = to_season if to_season is not None else current + 1
    count = await runner.refresh_weekends(start, end)
    return {"refreshed": count, "from_season": start, "to_season": end}


@router.post("/qualifying/{season}/{round_number}", status_code=202)
async def ingest_qualifying(
    season: int,
    round_number: int,
    runner: IngestRunner = Depends(get_runner),
    store: IngestionStore = Depends(get_store),
):
    """Fetch the grid for one round.

    Returns 409 rather than 500 when qualifying simply has not run yet — that is
    an expected state for an upcoming weekend, not a server fault.
    """
    weekends = await store.list_weekends(season, season)
    match = next((w for w in weekends if w["round"] == round_number), None)
    if match is None:
        raise HTTPException(
            status_code=404, detail="no timetable for {}-{}".format(season, round_number)
        )

    expected = ExpectedSession(
        season=season,
        round=round_number,
        race_name=match.get("race_name", ""),
        circuit=match.get("circuit", ""),
    )
    try:
        saved = await runner.ingest_qualifying(expected)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="qualifying data not available for {}-{}: {}".format(
                season, round_number, exc
            ),
        )
    return {"season": season, "round": round_number, "grid_rows": saved}


@router.post("/starting-grid/{season}/{round_number}", status_code=200)
async def refresh_starting_grid(
    season: int,
    round_number: int,
    runner: IngestRunner = Depends(get_runner),
    store: IngestionStore = Depends(get_store),
):
    """Apply the FIA's official starting grid to a round we already qualified.

    Cheap enough to poll: one PDF, no FastF1 session load. Returns 409 while the
    stewards have not published yet, which is the normal state for the first few
    hours after qualifying and not a fault — the caller keeps its provisional
    grid and tries again.
    """
    weekends = await store.list_weekends(season, season)
    match = next((w for w in weekends if w["round"] == round_number), None)
    if match is None:
        raise HTTPException(
            status_code=404, detail="no timetable for {}-{}".format(season, round_number)
        )

    expected = ExpectedSession(
        season=season,
        round=round_number,
        race_name=match.get("race_name", ""),
        circuit=match.get("circuit", ""),
    )
    try:
        return await runner.refresh_starting_grid(expected)
    except fia_documents.GridDocumentUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (fia_documents.GridDocumentUnreadable,
            grid_resolution.GridApplicationError) as exc:
        # 502: the document exists but the upstream form of it defeated us.
        # Distinct from 409 so a parser regression is not mistaken for "the
        # stewards are slow today".
        raise HTTPException(
            status_code=502,
            detail="official grid for {}-{} could not be applied: {}".format(
                season, round_number, exc
            ),
        )


def _span(settings: Settings):
    return season_range(settings)
