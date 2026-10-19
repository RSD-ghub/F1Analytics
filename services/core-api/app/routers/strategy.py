"""The strategy engine's API: a race plan for one car, with the guide beside it.

Public, like the forecasts. It spends no model calls — the plan is arithmetic
and the citations are a search — so there is nothing to meter, and a plan a
reader cannot see is a plan nobody can check.

The same loaders serve Bernie (``routers/bernie.py``), so the plan she talks
through is the plan this page shows.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.services.downstream import ServiceClient, gather_optional
from app.services.strategy import build_inputs, simulate
from app.services.strategy_engine import (
    Car,
    build_field,
    excerpt,
    find_driver,
    mentioned_driver,
    plan_race,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategy", tags=["strategy"])

#: A second passage is shown beside a call only when it scores this close to
#: the best one. Below that it is a weaker match, not a second argument.
SECOND_PASSAGE_SHARE = 0.75


async def load_race(settings: Settings, season: int, round_number: int) -> Dict[str, Any]:
    """Everything the engine plans from, fetched together; each piece optional."""
    ingestion = ServiceClient(
        "ingestion", settings.ingestion_service_url, settings.downstream_timeout_seconds
    )
    return await gather_optional(
        profile=ingestion.get("/circuits/{}/{}/strategy".format(season, round_number)),
        circuit=ingestion.get("/circuits/{}/{}".format(season, round_number)),
        qualifying=ingestion.get(
            "/data/qualifying", {"season": season, "round": round_number, "limit": 40}
        ),
        practice=ingestion.get(
            "/data/practice", {"season": season, "round": round_number, "limit": 60}
        ),
    )


def as_rows(value: Any) -> List[Dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def as_mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def build_plan(
    settings: Settings,
    season: int,
    round_number: int,
    driver: Optional[str] = None,
    question: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Car], Dict[str, Any]]:
    """Run the engine for a race: ``(plan, field, what was fetched)``.

    The car is the one named, else the first one a question mentions, else
    none — a plan for the race as a whole.
    """
    fetched = await load_race(settings, season, round_number)
    circuit = as_mapping(fetched.get("circuit"))
    official = circuit.get("official") or {}
    profile = fetched.get("profile") if isinstance(fetched.get("profile"), dict) else None
    field = build_field(as_rows(fetched.get("qualifying")), as_rows(fetched.get("practice")))
    car = find_driver(field, driver) if driver else mentioned_driver(field, question or "")

    def run() -> Dict[str, Any]:
        sim = simulate(build_inputs(profile, official.get("scheduled_laps")))
        return plan_race(sim, circuit.get("stats"), field, car)

    # A few hundred thousand steps of arithmetic: off the event loop.
    return await asyncio.to_thread(run), field, fetched


def _queries(plan: Dict[str, Any]) -> Dict[str, str]:
    """The guide question each section of a plan answers."""
    queries = {
        "race_character": plan["race_character"]["guide_query"],
        "plans": plan["plans_guide_query"],
        "targets": plan["targets_guide_query"],
        "start_tyre": plan["start_tyre"]["guide_query"],
        "safety_car": plan["safety_car"]["guide_query"],
    }
    for section in ("pit_stop", "undercut", "edge_cases"):
        query = (plan.get(section) or {}).get("guide_query")
        if query:
            queries[section] = query
    return queries


def citation(hit: Dict[str, Any], query: str) -> Dict[str, Any]:
    """A short excerpt with its source: a citation, not a reproduction."""
    return {
        "title": hit.get("title"),
        "author": hit.get("author"),
        "chapter": hit.get("chapter"),
        "chapter_title": hit.get("chapter_title"),
        "excerpt": excerpt(hit.get("text", ""), query),
    }


async def guide_citations(
    settings: Settings, queries: Dict[str, str]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[str]]:
    """The passage (or two) arguing for each section, and a note if none could be had."""
    ingestion = ServiceClient(
        "ingestion", settings.ingestion_service_url, settings.downstream_timeout_seconds
    )
    fetched = await gather_optional(**{
        section: ingestion.get("/guide/search", {"q": query, "limit": 2})
        for section, query in queries.items()
    })
    citations: Dict[str, List[Dict[str, Any]]] = {}
    for section, query in queries.items():
        hits = as_rows(fetched.get(section))
        if not hits:
            continue
        best = hits[0].get("score") or 0.0
        chosen = [hits[0]] + [
            h for h in hits[1:]
            if (h.get("score") or 0.0) >= best * SECOND_PASSAGE_SHARE
            and h.get("chapter") != hits[0].get("chapter")
        ]
        citations[section] = [citation(h, query) for h in chosen]

    note = None
    if queries and len(fetched["_unavailable"]) == len(queries):
        note = "The strategy guide could not be searched, so the plan is shown without it."
    elif not citations:
        note = ("The strategy guide holds no book yet. Load one with "
                "scripts/ingest_strategy_guide.py.")
    return citations, note


@router.get("/{season}/{round_number}", response_model=Dict[str, Any])
async def race_strategy(
    season: int,
    round_number: int,
    driver: Optional[str] = Query(None, description="Driver name or surname to plan for"),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """The engine's plan for a race, and for one car if a driver is named.

    Always a plan: missing history falls back to labelled generic figures, as
    the simulator does, and a missing grid only removes the sections that need
    one. ``unavailable`` names what could not be fetched.
    """
    plan, field, fetched = await build_plan(settings, season, round_number, driver=driver)
    citations, note = (await guide_citations(settings, _queries(plan))) if plan else ({}, None)
    return {
        "season": season,
        "round": round_number,
        "plan": plan,
        "guide": citations,
        "guide_note": note,
        "field": [{"driver": c.driver, "team": c.team, "grid": c.grid} for c in field],
        "driver_not_found": bool(driver) and "driver" not in plan,
        "unavailable": fetched["_unavailable"],
    }
