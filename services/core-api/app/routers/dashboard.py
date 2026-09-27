"""BFF routes — one request per page, not one per panel.

The browser talks only to this service, so these endpoints exist to collapse
what would otherwise be four or five round trips into one. Each aggregates
across the internal services using ``gather_optional``, so a downstream outage
costs the panel that depends on it rather than the whole page, and the response
names what is missing instead of quietly omitting it.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.models.schemas import HealthSummary, SystemStatus
from app.routers.blog import WINDOW_PREFERENCE
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
    #: The track itself: shape, corners, and what our corpus knows about it.
    #: Optional on purpose — a circuit whose geometry was never derived still
    #: has a race history worth showing, and a brand-new venue has neither.
    circuit: Optional[Dict[str, Any]] = None
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


async def _with_character(clients, circuit):
    """Attach the model's archetype to a circuit from ingestion.

    Two services on purpose: ingestion owns the track's shape and its race
    record because it owns the data they come from, and prediction owns the
    label because the label is a modelling artifact. Merged here so a page
    makes one request — and merged loosely, so a circuit the artifact has
    never heard of keeps its map and its stats.
    """
    if not circuit or not circuit.get("circuit"):
        return circuit
    character = await gather_optional(
        character=clients["prediction"].get(
            "/circuits/{}".format(quote(circuit["circuit"], safe=""))
        )
    )
    return dict(circuit, character=character.get("character"))


@router.get("/circuit/{season}/{round_number}", response_model=Dict[str, Any])
async def circuit(
    season: int,
    round_number: int,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Any round's circuit. Public, like the rest of the weekend record."""
    clients = _clients(settings)
    fetched = await gather_optional(
        circuit=clients["ingestion"].get(
            "/circuits/{}/{}".format(season, round_number)
        )
    )
    found = fetched.get("circuit")
    if not found:
        raise HTTPException(status_code=404, detail="no circuit for that round")
    return await _with_character(clients, found)


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
        circuit=clients["ingestion"].get(
            "/circuits/{}/{}".format(season, round_number)
        ),
    )

    circuit = await _with_character(clients, fetched.get("circuit"))

    return NextRaceView(
        weekend=weekend,
        predictions=fetched.get("predictions") or [],
        qualifying_freshness=fetched.get("freshness"),
        circuit=circuit,
        unavailable=Unavailable(panels=fetched["_unavailable"]),
    )


class LastRaceView(BaseModel):
    """The race that just happened, and how the forecast held up.

    The front page spends most of the week between races. Without this it
    showed a circuit and "no forecast locked yet" for five days out of seven,
    while the thing the product actually claims — that it publishes first and
    is scored afterwards — had just been demonstrated and was nowhere on the
    page.
    """

    season: int
    round: int
    race_name: str = ""
    winner: str = ""
    winner_team: str = ""
    podium: List[Dict[str, Any]] = Field(default_factory=list)
    #: What the best-informed forecast said about the driver who actually won.
    called_winner: Optional[str] = None
    called_winner_probability: Optional[float] = None
    winner_probability: Optional[float] = None
    window: str = ""
    markets: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/backdrop", response_model=List[Dict[str, Any]])
async def backdrop(
    limit: int = Query(12, ge=1, le=30),
    settings: Settings = Depends(get_settings),
) -> List[Dict[str, Any]]:
    """Circuit shapes for the page background. Empty rather than failing.

    Decoration, so it degrades to nothing: a backdrop that 500s would take a
    page down to draw a watermark.
    """
    clients = _clients(settings)
    fetched = await gather_optional(
        outlines=clients["ingestion"].get("/circuits/outlines", {"limit": limit})
    )
    return fetched.get("outlines") or []


@router.get("/last-race", response_model=Optional[LastRaceView])
async def last_race(settings: Settings = Depends(get_settings)):
    """The most recent scored race. ``null`` when nothing has been scored yet.

    Driven from the scored record rather than from the calendar: a race that
    has run but not yet reconciled has nothing to say about accuracy, and a
    panel claiming otherwise would be the one place on the site where a result
    appears before it has been marked.
    """
    clients = _clients(settings)
    # /scores, not /track-record. The record is the aggregate across windows
    # and carries no individual races; the scores are the rows themselves.
    fetched = await gather_optional(
        scores=clients["scoring"].get("/scores", {"limit": 20})
    )
    scores = fetched.get("scores") or []
    if not scores:
        return None

    latest = max((s["season"], s["round"]) for s in scores)
    season, round_number = latest
    for_race = [s for s in scores if (s["season"], s["round"]) == latest]
    # The most-informed window that was actually scored, by the same
    # preference the rest of the product uses.
    best = next(
        (s for w in WINDOW_PREFERENCE for s in for_race if s["window"] == w),
        for_race[0],
    )

    detail = await gather_optional(
        results=clients["ingestion"].get(
            "/data/results", {"season": season, "round": round_number, "limit": 30}
        ),
        predictions=clients["prediction"].get(
            "/predictions/{}/{}".format(season, round_number)
        ),
    )
    results = sorted(
        [r for r in (detail.get("results") or []) if (r.get("position") or 0) > 0],
        key=lambda r: r["position"],
    )
    if not results:
        return None

    winner = results[0]
    forecast = next(
        (p for p in (detail.get("predictions") or [])
         if p.get("window") == best["window"]),
        None,
    )
    called, called_p, winner_p = None, None, None
    if forecast:
        rows = forecast.get("driver_probabilities") or []
        ranked = sorted(rows, key=lambda r: -(r.get("p_win") or 0))
        if ranked and ranked[0].get("p_win") is not None:
            called = ranked[0]["driver"]
            called_p = ranked[0]["p_win"]
        match = next((r for r in rows if r["driver"] == winner["driver"]), None)
        if match:
            winner_p = match.get("p_win")

    return LastRaceView(
        season=season,
        round=round_number,
        race_name=winner.get("race_name", ""),
        winner=winner.get("driver", ""),
        winner_team=winner.get("team", ""),
        podium=[
            {"position": r["position"], "driver": r.get("driver", ""),
             "team": r.get("team", "")}
            for r in results[:3]
        ],
        called_winner=called,
        called_winner_probability=called_p,
        winner_probability=winner_p,
        window=best["window"],
        markets=best.get("markets") or [],
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
