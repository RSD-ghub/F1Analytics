"""One Blog endpoints.

Public on purpose. The weekend record is the evidence behind a published
forecast, and putting it behind a login would defeat the point of publishing a
track record at all.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

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
