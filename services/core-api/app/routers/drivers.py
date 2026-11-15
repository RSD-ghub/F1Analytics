"""Driver pages and the post-race pit stop review. Public, like the record."""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.services.downstream import ServiceClient
from app.services.driver_pages import driver_profile, pit_stop_review, season_drivers
from app.services.race_story import race_story

router = APIRouter(tags=["drivers"])

#: Enough rows for a whole season (24 races x 22 cars) with room to spare.
SEASON_ROWS = 1000
#: A long career is ~400 starts.
CAREER_ROWS = 1000


def _ingestion(settings: Settings) -> ServiceClient:
    return ServiceClient("ingestion", settings.ingestion_service_url,
                         settings.downstream_timeout_seconds)


@router.get("/drivers/{season}", response_model=List[Dict[str, Any]])
async def drivers(season: int, settings: Settings = Depends(get_settings)):
    rows = await _ingestion(settings).get(
        "/data/results", {"season": season, "limit": SEASON_ROWS}
    )
    return season_drivers(rows or [])


@router.get("/drivers/{season}/{driver}", response_model=Dict[str, Any])
async def driver(season: int, driver: str, settings: Settings = Depends(get_settings)):
    rows = await _ingestion(settings).get(
        "/data/results", {"driver": driver, "limit": CAREER_ROWS}
    )
    profile = driver_profile(driver, season, rows or [])
    if profile is None:
        raise HTTPException(status_code=404, detail="no races recorded for that driver")
    return profile


@router.get("/race-review/{season}/{round_number}", response_model=Dict[str, Any])
async def race_review(
    season: int, round_number: int, settings: Settings = Depends(get_settings)
):
    """Stints and stops for a finished race. Stints and stops are optional:
    seasons before 2018 have results but no lap data, and say so."""
    client = _ingestion(settings)
    scope = {"season": season, "round": round_number, "limit": 200}
    results = await client.get("/data/results", scope)
    if not results:
        raise HTTPException(status_code=404, detail="no result recorded for that race")
    stints = await client.get("/data/stints", scope)
    stops = await client.get("/data/pit_stops", scope)
    return pit_stop_review(results, stints or [], stops or [])


#: A whole race of laps: 22 cars x ~78 laps, well inside ingestion's cap.
RACE_LAP_ROWS = 5000


@router.get("/race-story/{season}/{round_number}", response_model=Dict[str, Any])
async def race_story_page(
    season: int, round_number: int, settings: Settings = Depends(get_settings)
):
    """A finished race lap by lap, with the forecasts we locked beside it.

    Everything comes from stored data, so it is quick. The lap-by-lap live
    forecast is ``/live-replay``, asked for separately because it simulates.
    """
    client = _ingestion(settings)
    scope = {"season": season, "round": round_number, "limit": 200}
    results = await client.get("/data/results", scope)
    if not results:
        raise HTTPException(status_code=404, detail="no result recorded for that race")
    laps = await client.get("/data/laps", dict(scope, limit=RACE_LAP_ROWS))
    stops = await client.get("/data/pit_stops", scope)
    messages = await client.get("/data/race_control", dict(scope, limit=2000))
    prediction = ServiceClient("prediction", settings.prediction_service_url,
                               settings.downstream_timeout_seconds)
    try:
        predictions = await prediction.get("/predictions/{}/{}".format(season, round_number))
    except Exception:  # the race is still worth showing without them
        predictions = []
    return race_story(results, laps or [], stops or [], messages or [], predictions or [])
