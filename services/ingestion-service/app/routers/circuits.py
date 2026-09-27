"""Read and derive circuit geometry.

Two verbs with deliberately different costs. ``GET`` serves a stored map and
computes the stats around it; it is what the public page calls. ``POST``
derives the geometry, which means a FastF1 session load with telemetry — the
most expensive single call this service makes — and is never reachable from a
page a visitor can open.

Splitting them that way is the point. Deriving on a cache miss would have been
fewer lines and would have made the home page a way to spend the service's
rate limit.
"""

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_runner, get_store
from app.services.circuit_map import CircuitMapUnavailable, slug as circuit_slug
from app.services.circuit_stats import summarise
from app.services import f1_site
from app.services.ingest_runner import IngestRunner
from app.services.storage import IngestionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/circuits", tags=["circuits"])


async def _weekend(store: IngestionStore, season: int, round_number: int):
    weekends = await store.list_weekends(season, season)
    match = next((w for w in weekends if w["round"] == round_number), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="no timetable for {}-{}".format(season, round_number),
        )
    return match


# Registered before the "/{season}/{round_number}" routes below.
#
# FastAPI matches in registration order, and "official" is a perfectly good
# candidate for a path parameter named ``season`` — declared after them, this
# route was unreachable and the request failed trying to parse "official" as
# an integer.
@router.post("/official/{season}", response_model=Dict[str, Any])
async def refresh_official(
    season: int,
    store: IngestionStore = Depends(get_store),
) -> Dict[str, Any]:
    """Read each circuit's own page and store the figures it states.

    Facts only — official name, length, scheduled laps and distance, first
    season, and the lap record with its holder. The track illustrations on
    those pages are F1's artwork rather than measurements, and this project
    publishes what it draws from telemetry instead.

    Keyed on the circuit rather than the round, joined by the short name the
    page gives, which is the same string the schedule uses as a location. One
    request per circuit, once a season.
    """
    try:
        slugs = await asyncio.to_thread(f1_site.fetch_season_slugs, season)
    except f1_site.F1SiteUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    stored, failed = [], []
    for race_slug in slugs:
        try:
            facts = await asyncio.to_thread(
                f1_site.fetch_circuit_facts, season, race_slug
            )
        except f1_site.F1SiteUnavailable as exc:
            logger.info("no facts for %s: %s", race_slug, exc)
            failed.append(race_slug)
            continue
        key = circuit_slug(facts["short_name"])
        await store.save_circuit_official(key, facts)
        stored.append(key)
        # One page a second. The volume is trivial either way; there is no
        # reason to arrive as a burst.
        await asyncio.sleep(1.0)

    return {"season": season, "stored": sorted(stored), "failed": failed}

@router.get("/{season}/{round_number}", response_model=Dict[str, Any])
async def circuit(
    season: int,
    round_number: int,
    store: IngestionStore = Depends(get_store),
) -> Dict[str, Any]:
    """A round's circuit: its shape if we have derived one, and its record.

    The map and the stats are returned together but fail apart. A circuit we
    have raced at for years and never derived geometry for still has a record
    worth reading, so ``map`` is null and ``stats`` is populated — the page
    renders the numbers and says the layout is unavailable, rather than showing
    nothing.
    """
    weekend = await _weekend(store, season, round_number)
    name = weekend.get("circuit") or ""

    history = await store.circuit_history(name)
    stored = await store.circuit_map(name) or {}
    official = stored.pop("official", None)
    # Geometry only counts as a map when it has geometry. A document holding
    # nothing but official facts is a circuit we could not draw, and saying
    # ``map: null`` lets the page render the record without one instead of
    # receiving an empty outline and deciding for itself what that means.
    has_layout = len(stored.get("outline") or []) > 1

    return {
        "season": season,
        "round": round_number,
        "race_name": weekend.get("race_name", ""),
        "circuit": name,
        "country": weekend.get("country", ""),
        "map": stored if has_layout else None,
        "official": official,
        "stats": summarise(name, history["results"], history["laps"]),
    }


@router.post("/{season}/{round_number}", response_model=Dict[str, Any])
async def derive(
    season: int,
    round_number: int,
    force: bool = False,
    store: IngestionStore = Depends(get_store),
    runner: IngestRunner = Depends(get_runner),
) -> Dict[str, Any]:
    """Derive and store this circuit's geometry from session telemetry.

    Idempotent and skipped when a map already exists, because the answer does
    not change: a track's shape is the same every visit, and re-deriving it
    spends a telemetry load to redraw the same line. ``force`` is for the case
    where a circuit has actually been altered.
    """
    weekend = await _weekend(store, season, round_number)
    name = weekend.get("circuit") or ""

    existing = await store.circuit_map(name)
    if existing and not force:
        return {"circuit": name, "derived": False, "reason": "already stored"}

    # This weekend first, then every earlier visit. The current event is the
    # one least likely to have telemetry published, and a circuit drawn from
    # last season is the same circuit.
    visits = [(season, weekend.get("race_name", ""))]
    visits += [
        visit for visit in await store.circuit_visits(name)
        if visit[0] != season
    ]
    try:
        derived = await runner.derive_circuit_map(name, visits)
    except CircuitMapUnavailable as exc:
        # 409 rather than 500: the request was valid and the answer is "not
        # from this weekend". A season with no position data is a fact about
        # the archive, not a fault in this service.
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("circuit derivation failed for %s", name)
        raise HTTPException(status_code=502, detail=str(exc))

    await store.save_circuit_map(derived.as_document())
    return {
        "circuit": name,
        "derived": True,
        "source": "{} {}".format(derived.source_season, derived.source_session),
        "corners": len(derived.corners),
        "outline_points": len(derived.outline),
    }
