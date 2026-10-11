"""One Blog endpoints.

Public on purpose. The weekend record is the evidence behind a published
forecast, and putting it behind a login would defeat the point of publishing a
track record at all.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import get_llm, get_usage
from app.models.blog import WeekendBlog
from app.services import blog as builder
from app.services.bernie import Bernie
from app.services.downstream import ServiceClient, gather_optional
from app.services.usage import UsageStore

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


class FeedEntry(BaseModel):
    """One entry inside a weekend, shown when the card is opened."""

    id: str
    kind: str = ""
    kind_label: str = ""
    headline: str = ""
    facts: List[Dict[str, Any]] = Field(default_factory=list)


class FeedItem(BaseModel):
    """One race weekend, as a single card."""

    id: str
    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    race_start_utc: Optional[str] = None
    image: Optional[str] = None
    #: What happened, in two sentences, written by the model over the same
    #: entries the card carries. Absent when the model is unavailable — the
    #: card still has its headline and its numbers.
    summary: str = ""
    headline: str = ""
    #: The three figures a reader wants without opening anything: who won,
    #: whether we called it, and how the forecast scored.
    winner: str = ""
    called_winner: str = ""
    win_skill: Optional[float] = None
    entries: List[FeedEntry] = Field(default_factory=list)


KIND_LABEL = {
    "paddock_news": "Paddock news",
    "practice_report": "Practice",
    "qualifying_report": "Qualifying",
    "forecast": "Forecast",
    "result": "Result",
}


@router.get("/feed", response_model=List[FeedItem])
async def feed(
    background: BackgroundTasks,
    limit: int = Query(8, ge=1, le=24),
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    usage: UsageStore = Depends(get_usage),
) -> List[FeedItem]:
    """The weekend record, one card per race weekend, newest first.

    It was one card per *entry*, which gave a single grand prix five cards —
    qualifying, three forecasts and the result — each with the same photograph
    of the same circuit. That is not a feed, it is a timeline with the
    weekends taken out of it.

    So: one card a weekend, carrying what a reader wants without opening
    anything — who won, whether we called it, how the forecast scored — with
    the entries underneath for whoever wants them.

    The summary is written by the model, and the request never waits for it.
    Ten weekends is ten model calls on a cold cache, and this page showed a
    skeleton for the whole of it — the same mistake the weekend page had, where
    the facts sat ready while the prose was written. A card whose summary has
    not been produced yet renders without one; it is generated after the
    response goes out and is there on the next load.
    """
    ingestion, prediction = _clients(settings)
    scoring = ServiceClient(
        "scoring", settings.scoring_service_url, settings.downstream_timeout_seconds
    )
    chosen = datetime.now(timezone.utc).year

    fetched = await gather_optional(
        calendar=ingestion.get("/forward/calendar/{}".format(chosen)),
        scores=scoring.get("/scores", {"season": chosen, "limit": 200}),
    )
    scores = fetched.get("scores") or []
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

    bernie = Bernie(llm)
    items: List[FeedItem] = []
    for round_number, weekend in rounds[:limit]:
        try:
            entries, race_name, _circuit = await _assemble(
                settings, chosen, round_number
            )
        except Exception:
            logger.exception("feed skipped %s-%s", chosen, round_number)
            continue
        entries = [e for e in entries if e]
        # What the weekend produced, as distinct from what was reported about
        # it. A card needs at least one of ours to exist, and the summary is
        # written over ours alone: it says what happened and how the forecast
        # fared, and headlines arriving through race week would otherwise
        # change its cache key, and its subject, every half hour.
        record = [
            e for e in entries
            if getattr(e.kind, "value", e.kind) != "paddock_news"
        ]
        if not record:
            continue

        found = await gather_optional(
            circuit=ingestion.get("/circuits/{}/{}".format(chosen, round_number)),
            results=ingestion.get(
                "/data/results",
                {"season": chosen, "round": round_number, "limit": 5},
            ),
        )
        imagery = ((found.get("circuit") or {}).get("imagery")) or []
        classified = sorted(
            [r for r in (found.get("results") or []) if (r.get("position") or 0) > 0],
            key=lambda r: r["position"],
        )

        # The best-informed window that was scored for this race.
        for_race = [s for s in scores if s.get("round") == round_number]
        best = next(
            (s for w in WINDOW_PREFERENCE for s in for_race if s["window"] == w),
            for_race[0] if for_race else None,
        )
        win_skill = None
        if best:
            market = next(
                (m for m in (best.get("markets") or []) if m["market"] == "win"), None
            )
            win_skill = market["skill_vs_baseline"] if market else None

        called = ""
        if best:
            forecast = next(
                (e for e in entries
                 if getattr(e.kind, "value", e.kind) == "forecast"), None
            )
            if forecast:
                favourite = next(
                    (f for f in forecast.facts if f.label == "Most likely winner"), None
                )
                called = favourite.value if favourite else ""

        result = next(
            (e for e in entries if getattr(e.kind, "value", e.kind) == "result"), None
        )
        item = FeedItem(
            id="{}-{}".format(chosen, round_number),
            season=chosen,
            round=round_number,
            race_name=race_name or weekend.get("race_name", ""),
            circuit=weekend.get("circuit", ""),
            race_start_utc=weekend.get("race_start_utc"),
            image=imagery[0]["url"] if imagery else None,
            headline=result.headline if result else record[-1].headline,
            winner=classified[0].get("driver", "") if classified else "",
            called_winner=called,
            win_skill=win_skill,
            entries=[
                FeedEntry(
                    id=e.entry_id,
                    kind=getattr(e.kind, "value", e.kind),
                    kind_label=KIND_LABEL.get(
                        getattr(e.kind, "value", e.kind), "Weekend"
                    ),
                    headline=e.headline,
                    facts=[f.model_dump() for f in (e.facts or [])[:4]],
                )
                for e in entries
            ],
            summary=await _cached_summary(usage, chosen, round_number, record),
        )
        if not item.summary and bernie.available:
            background.add_task(
                _write_summary, bernie, usage, chosen, round_number, record,
            )
        items.append(item)

    return items


def _summary_key(season: int, round_number: int, entries) -> str:
    """Keyed on the entry count as well as the round.

    A race summarised after qualifying and then finished would otherwise keep
    a summary describing a weekend that had not happened yet.
    """
    return UsageStore.key("weekend-summary", season, round_number, len(entries))


async def _cached_summary(usage, season: int, round_number: int, entries) -> str:
    cached = await usage.cached(_summary_key(season, round_number, entries))
    return (cached or {}).get("summary", "")


async def _write_summary(bernie, usage, season: int, round_number: int, entries):
    """Produce a weekend's two sentences, after the response has gone out."""
    facts = {
        "race": "{} round {}".format(season, round_number),
        "what the weekend produced": [
            "{}: {}".format(
                KIND_LABEL.get(getattr(e.kind, "value", e.kind), "Entry"), e.headline
            )
            for e in entries
        ],
        "the figures": [
            "{} {} {}".format(f.label, f.value, f.detail).strip()
            for e in entries for f in (e.facts or [])[:4]
        ],
    }
    try:
        summary = await bernie.summarise(facts)
    except Exception:
        # Decoration on a card. The weekend still has its headline and its
        # figures, and the next load will try again.
        logger.warning("no summary for %s-%s", season, round_number, exc_info=True)
        return
    await usage.store(_summary_key(season, round_number, entries), {"summary": summary})


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
        news=ingestion.get(
            "/news/weekend/{}/{}".format(season, round_number), {"limit": 12}
        ),
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
        builder.news_entry(season, round_number, race_name, fetched.get("news")),
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
