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


class FeedItem(BaseModel):
    """One thing that happened, as it appears in the feed."""

    id: str
    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    kind: str = ""
    kind_label: str = ""
    headline: str = ""
    summary: str = ""
    occurred_at: Optional[str] = None
    image: Optional[str] = None
    #: The two or three figures worth showing on a card. The full entry has
    #: more; a card that reproduces all of them is the timeline again.
    facts: List[Dict[str, Any]] = Field(default_factory=list)


KIND_LABEL = {
    "practice_report": "Practice",
    "qualifying_report": "Qualifying",
    "forecast": "Forecast",
    "result": "Result",
}


@router.get("/feed", response_model=List[FeedItem])
async def feed(
    limit: int = Query(24, ge=1, le=60),
    settings: Settings = Depends(get_settings),
) -> List[FeedItem]:
    """The weekend record as a stream of items, newest first.

    The index lists weekends; this lists what happened inside them. A reader
    arriving at a blog wants to see what there is to read, not a table of
    contents pointing at eleven rounds any of which might be empty.

    Assembled from the same entry builders the weekend page uses, so a card
    and the page it opens cannot disagree. Narration is deliberately not
    requested: it costs thirteen seconds a weekend and a card shows a
    headline, not prose.
    """
    ingestion, prediction = _clients(settings)
    chosen = datetime.now(timezone.utc).year

    fetched = await gather_optional(
        calendar=ingestion.get("/forward/calendar/{}".format(chosen)),
    )
    now = datetime.now(timezone.utc)
    rounds = []
    for weekend in (fetched.get("calendar") or []):
        start = weekend.get("race_start_utc")
        if not start:
            continue
        try:
            when = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when <= now:
            rounds.append((weekend["round"], weekend))
    rounds.sort(reverse=True)

    items: List[FeedItem] = []
    # Newest rounds first, stopping as soon as the page is full. Each weekend
    # is a handful of stored queries with no narration, so this is cheap — but
    # there is no reason to build the whole season to show twenty-four cards.
    for round_number, weekend in rounds:
        if len(items) >= limit:
            break
        try:
            entries, race_name, _circuit = await _assemble(
                settings, chosen, round_number
            )
        except Exception:
            logger.exception("feed skipped %s-%s", chosen, round_number)
            continue
        entries = [e for e in entries if e]
        found = await gather_optional(
            circuit=ingestion.get("/circuits/{}/{}".format(chosen, round_number))
        )
        imagery = ((found.get("circuit") or {}).get("imagery")) or []
        image = imagery[0]["url"] if imagery else None

        for entry in entries:
            # ``_assemble`` returns the builders' own models, not dictionaries.
            kind = getattr(entry.kind, "value", entry.kind)
            items.append(FeedItem(
                id=entry.entry_id,
                season=chosen,
                round=round_number,
                race_name=entry.race_name or race_name or weekend.get("race_name", ""),
                circuit=weekend.get("circuit", ""),
                kind=kind,
                kind_label=KIND_LABEL.get(kind, "Weekend"),
                headline=entry.headline,
                summary=entry.summary,
                occurred_at=(
                    entry.occurred_at.isoformat()
                    if hasattr(entry.occurred_at, "isoformat") else entry.occurred_at
                ),
                image=image,
                # Two or three figures, not the whole entry: a card that
                # reproduces every fact is the timeline again.
                facts=[f.model_dump() for f in (entry.facts or [])[:3]],
            ))

    return items[:limit]


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
    entries, race_name, circuit = await _assemble(settings, season, round_number)

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


async def _assemble(settings: Settings, season: int, round_number: int):
    """Build a weekend's entries from stored data, without narrating them.

    Shared by the page and the feed. A card and the page it opens onto are
    then the same computation, which is the only way they cannot drift apart —
    and the feed gets the entries without paying for prose it does not show.
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

    return entries, race_name, circuit
