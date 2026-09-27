"""One Blog endpoints.

Public on purpose. The weekend record is the evidence behind a published
forecast, and putting it behind a login would defeat the point of publishing a
track record at all.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import get_llm
from app.models.blog import WeekendBlog
from app.services import blog as builder
from app.services.bernie import Bernie
from app.services.downstream import ServiceClient, gather_optional

logger = logging.getLogger(__name__)


#: Forecast windows, most-informed first. Used wherever one prediction has to
#: stand in for the weekend: the final-grid call knows the confirmed starting
#: order, the post-quali call knows a provisional one, and the pre-quali call
#: knows no grid at all. Picking "post_quali" by name silently ignored the
#: better forecast once the final-grid window existed.
WINDOW_PREFERENCE = ("final_grid", "post_quali", "pre_quali")


def most_informed(predictions):
    """The best available forecast for a race, or None."""
    for window in WINDOW_PREFERENCE:
        found = next((p for p in predictions if p.get("window") == window), None)
        if found is not None:
            return found
    return predictions[0] if predictions else None


router = APIRouter(prefix="/blog", tags=["blog"])


def _clients(settings: Settings):
    return (
        ServiceClient("ingestion", settings.ingestion_service_url,
                      settings.downstream_timeout_seconds),
        ServiceClient("prediction", settings.prediction_service_url,
                      settings.downstream_timeout_seconds),
    )


class WeekendCard(BaseModel):
    """One round, as it appears in the index."""

    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    country: str = ""
    race_start_utc: Optional[str] = None
    #: Whether the race has run, by the clock rather than by what we hold. A
    #: race that finished an hour ago is in the past even if nothing has been
    #: ingested for it yet, and saying otherwise would be the index telling a
    #: different story from the calendar.
    has_run: bool = False
    #: What there is to read. A card that promises a report and opens onto
    #: "nothing to report yet" is worse than one that says so up front.
    forecasts: int = 0
    scored: bool = False


class BlogIndex(BaseModel):
    season: int
    seasons: List[int] = Field(default_factory=list)
    weekends: List[WeekendCard] = Field(default_factory=list)


@router.get("", response_model=BlogIndex)
async def index(
    season: Optional[int] = Query(None),
    settings: Settings = Depends(get_settings),
) -> BlogIndex:
    """The weekend record, round by round.

    One Blog had no front door. Its entries live at /blog/{season}/{round},
    which is only reachable by already knowing a season and a round — so the
    part of the product meant to be a highlight could not be navigated to at
    all.

    Ordered as a record rather than as a calendar: the races that have
    happened come first, newest at the top, and the ones still to come follow
    in the order they will be run. Plain newest-first put round 23 at the top
    in September, two months before anyone could read anything about it.
    """
    ingestion, prediction = _clients(settings)
    scoring = ServiceClient(
        "scoring", settings.scoring_service_url, settings.downstream_timeout_seconds
    )
    chosen = season or datetime.now(timezone.utc).year

    fetched = await gather_optional(
        calendar=ingestion.get("/forward/calendar/{}".format(chosen)),
        seasons=ingestion.get("/data/seasons"),
        scores=scoring.get("/scores", {"season": chosen, "limit": 200}),
    )

    # Which rounds were scored, in one pass rather than a call per round.
    scored_rounds = {
        s["round"] for s in (fetched.get("scores") or []) if s.get("round")
    }
    now = datetime.now(timezone.utc)

    cards: List[WeekendCard] = []
    for weekend in (fetched.get("calendar") or []):
        start = weekend.get("race_start_utc")
        has_run = False
        if start:
            try:
                has_run = datetime.fromisoformat(
                    start.replace("Z", "+00:00")
                ) <= now
            except ValueError:
                has_run = False
        cards.append(WeekendCard(
            season=weekend["season"],
            round=weekend["round"],
            race_name=weekend.get("race_name", ""),
            circuit=weekend.get("circuit", ""),
            country=weekend.get("country", ""),
            race_start_utc=start,
            has_run=has_run,
            scored=weekend["round"] in scored_rounds,
        ))

    seasons = sorted(fetched.get("seasons") or [], reverse=True)
    return BlogIndex(
        season=chosen,
        seasons=seasons or [chosen],
        # Run races descending, then upcoming ascending.
        weekends=sorted(cards, key=lambda c: (not c.has_run, -c.round if c.has_run else c.round)),
    )


@router.get("/{season}/{round_number}", response_model=WeekendBlog)
async def weekend(
    season: int,
    round_number: int,
    narrate: bool = Query(True, description="Attach Bernie's prose over the facts."),
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
) -> WeekendBlog:
    """The weekend's timeline: practice, forecast, and what we would not claim.

    Assembled with ``gather_optional`` so a downstream outage costs one panel
    rather than the whole page — a reader should still get the practice report
    when prediction-service is down.
    """
    ingestion, prediction = _clients(settings)

    fetched = await gather_optional(
        practice=ingestion.get(
            "/data/practice",
            {"season": season, "round": round_number, "limit": 200},
        ),
        results=ingestion.get(
            "/data/results", {"season": season, "round": round_number, "limit": 100}
        ),
        qualifying=ingestion.get(
            "/data/qualifying",
            {"season": season, "round": round_number, "limit": 100},
        ),
        predictions=prediction.get("/predictions/{}/{}".format(season, round_number)),
    )
    if fetched["_unavailable"]:
        logger.warning(
            "blog %s-%s built without: %s",
            season, round_number, fetched["_unavailable"],
        )

    results: List[Dict[str, Any]] = fetched.get("results") or []
    race_name = next((r.get("race_name", "") for r in results if r.get("race_name")), "")
    circuit = next((r.get("circuit", "") for r in results if r.get("circuit")), "")

    predictions = fetched.get("predictions") or []
    entries = [
        builder.practice_entry(
            season, round_number, race_name, fetched.get("practice") or []
        ),
        builder.qualifying_entry(
            season, round_number, race_name, fetched.get("qualifying") or []
        ),
    ]
    entries.extend(builder.forecast_entry(row, race_name) for row in predictions)
    # The result entry compares against the best-informed forecast we made —
    # the one placed on the confirmed grid when that window fired.
    post_quali = most_informed(predictions)
    entries.append(
        builder.result_entry(
            season, round_number, race_name, results, post_quali
        )
    )

    bernie = Bernie(llm)
    if narrate and bernie.available:
        entries = [
            await builder.narrate(bernie, entry) if entry else None
            for entry in entries
        ]

    return builder.assemble(
        season, round_number, race_name, circuit, entries,
        narration_available=bernie.available,
    )
