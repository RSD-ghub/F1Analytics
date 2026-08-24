"""BFF routes — one request per page, not one per panel.

The browser talks only to this service, so these endpoints exist to collapse
what would otherwise be four or five round trips into one. Each aggregates
across the internal services using ``gather_optional``, so a downstream outage
costs the panel that depends on it rather than the whole page, and the response
names what is missing instead of quietly omitting it.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.models.schemas import HealthSummary, SystemStatus
from app.services.downstream import ServiceClient, gather_optional

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


class Unavailable(BaseModel):
    """Named gaps in an aggregated response.

    Rendering a page with a silently missing panel is worse than saying which
    one is missing — the reader cannot tell the difference between "no data"
    and "broken".
    """

    panels: List[str] = Field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.panels)


class NextRaceView(BaseModel):
    weekend: Optional[Dict[str, Any]] = None
    predictions: List[Dict[str, Any]] = Field(default_factory=list)
    qualifying_freshness: Optional[Dict[str, Any]] = None
    unavailable: Unavailable = Field(default_factory=Unavailable)


class TrackRecordView(BaseModel):
    record: Optional[Dict[str, Any]] = None
    recent_scores: List[Dict[str, Any]] = Field(default_factory=list)
    unavailable: Unavailable = Field(default_factory=Unavailable)


def _clients(settings: Settings):
    return {
        "ingestion": ServiceClient("ingestion", settings.ingestion_service_url,
                                   settings.downstream_timeout_seconds),
        "prediction": ServiceClient("prediction", settings.prediction_service_url,
                                    settings.downstream_timeout_seconds),
        "scoring": ServiceClient("scoring", settings.scoring_service_url,
                                 settings.downstream_timeout_seconds),
    }


@router.get("/status", response_model=SystemStatus)
async def status(settings: Settings = Depends(get_settings)) -> SystemStatus:
    """Which internal services are reachable. Never 503s — that is the point."""
    clients = _clients(settings)
    checks = await gather_optional(
        **{name: client.healthy() for name, client in clients.items()}
    )
    services = [
        HealthSummary(
            service=name,
            reachable=bool(checks.get(name)),
            detail="" if checks.get(name) else "unreachable",
        )
        for name in clients
    ]
    return SystemStatus(
        services=services, all_reachable=all(s.reachable for s in services)
    )


@router.get("/next-race", response_model=NextRaceView)
async def next_race(settings: Settings = Depends(get_settings)) -> NextRaceView:
    """The upcoming weekend, with whatever forecasts are already locked."""
    clients = _clients(settings)

    upcoming = await gather_optional(weekend=clients["ingestion"].get("/forward/next"))
    weekend = upcoming.get("weekend")
    if not weekend:
        return NextRaceView(unavailable=Unavailable(panels=["weekend"]))

    season, round_number = weekend["season"], weekend["round"]
    fetched = await gather_optional(
        predictions=clients["prediction"].get(
            "/predictions/{}/{}".format(season, round_number)
        ),
        freshness=clients["ingestion"].get(
            "/forward/qualifying-freshness/{}/{}".format(season, round_number)
        ),
    )
    return NextRaceView(
        weekend=weekend,
        predictions=fetched.get("predictions") or [],
        qualifying_freshness=fetched.get("freshness"),
        unavailable=Unavailable(panels=fetched["_unavailable"]),
    )


@router.get("/track-record", response_model=TrackRecordView)
async def track_record(
    season: Optional[int] = Query(None),
    settings: Settings = Depends(get_settings),
) -> TrackRecordView:
    """The public accuracy record.

    Deliberately unauthenticated. A track record behind a login is a marketing
    claim; the whole point is that anyone can check it.
    """
    clients = _clients(settings)
    params = {"season": season} if season is not None else None
    fetched = await gather_optional(
        record=clients["scoring"].get("/track-record", params),
        scores=clients["scoring"].get("/scores", dict(params or {}, limit=20)),
    )
    return TrackRecordView(
        record=fetched.get("record"),
        recent_scores=fetched.get("scores") or [],
        unavailable=Unavailable(panels=fetched["_unavailable"]),
    )


@router.get("/championship/{season}")
async def championship(
    season: int, settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    """Title probabilities. Passes through, since there is nothing to aggregate."""
    clients = _clients(settings)
    return await clients["prediction"].get("/championship/{}".format(season))
