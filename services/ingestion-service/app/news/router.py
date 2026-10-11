"""Read API over paddock news, and the manual refresh.

Its own prefix for the reason the regulations have one: these are headlines
from outlets, not an ingested F1 dataset, and they are searched by question or
by time rather than filtered by season and round.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.dependencies import get_news_store, get_store
from app.news import source
from app.news.store import NewsStore, weekend_window
from app.services.storage import IngestionStore

router = APIRouter(prefix="/news", tags=["news"])


async def _weekend_bounds(
    weekends: IngestionStore, season: int, round_number: int
) -> Tuple[datetime, datetime]:
    """The news window for a round, from the calendar this service already holds.

    Resolved here rather than by the caller because this service owns the
    calendar; core-api asking for "round 18's news" should not need to know
    when round 18 starts, or what a race week is.
    """
    for weekend in await weekends.list_weekends(season, season):
        if weekend.get("round") != round_number:
            continue
        start = weekend.get("race_start_utc")
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if isinstance(start, datetime):
            return weekend_window(start)
        break
    raise HTTPException(
        status_code=404,
        detail="no race start known for {}-{}".format(season, round_number),
    )


@router.get("", response_model=List[Dict[str, Any]])
async def latest(
    limit: int = Query(20, ge=1, le=50),
    source_key: Optional[str] = Query(None, alias="source"),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    store: NewsStore = Depends(get_news_store),
) -> List[Dict[str, Any]]:
    """Headlines, newest first."""
    return await store.latest(limit=limit, source=source_key, since=since, until=until)


@router.get("/weekend/{season}/{round_number}", response_model=Dict[str, Any])
async def weekend(
    season: int,
    round_number: int,
    limit: int = Query(10, ge=1, le=50),
    store: NewsStore = Depends(get_news_store),
    weekends: IngestionStore = Depends(get_store),
) -> Dict[str, Any]:
    """Headlines from one race week, newest first, with the window they came from.

    The window is returned alongside the items so a page can say which week the
    stories came from, rather than leave a reader to guess why last week's
    story is not there.
    """
    since, until = await _weekend_bounds(weekends, season, round_number)
    items = await store.latest(limit=limit, since=since, until=until)
    return {"season": season, "round": round_number,
            "since": since, "until": until, "items": items}


@router.get("/search", response_model=List[Dict[str, Any]])
async def search(
    q: str = Query(..., min_length=2, description="Question or keywords"),
    season: Optional[int] = Query(None),
    round_number: Optional[int] = Query(None, alias="round"),
    limit: int = Query(5, ge=1, le=20),
    store: NewsStore = Depends(get_news_store),
    weekends: IngestionStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    """Headlines matching a question, best first, each with its ``score``.

    Scoped to one race week when ``season`` and ``round`` are given. Bernie
    asked about Singapore should not be handed a story from Monza.
    """
    since = until = None
    if season is not None and round_number is not None:
        since, until = await _weekend_bounds(weekends, season, round_number)
    return await store.search(q, since=since, until=until, limit=limit)


@router.get("/sources", response_model=Dict[str, Any])
async def sources() -> Dict[str, Any]:
    """Which outlets are read, and what the last pass over them did."""
    last = source.last_refresh
    return {
        "feeds": [{"key": f.key, "outlet": f.outlet, "url": f.url} for f in source.FEEDS],
        "last_refresh": None if last is None else {
            "finished_at": last.finished_at, "new": last.new, "feeds": last.feeds,
        },
    }


@router.post("/refresh", response_model=Dict[str, Any])
async def refresh(
    store: NewsStore = Depends(get_news_store),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Read every feed now rather than waiting for the scheduler."""
    summary = await source.refresh(store, retention_days=settings.news_retention_days)
    return {"finished_at": summary.finished_at, "new": summary.new, "feeds": summary.feeds}
