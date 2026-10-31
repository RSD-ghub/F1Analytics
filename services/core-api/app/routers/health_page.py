"""The data health page: is the data behind the forecasts whole and fresh?

One public read for an operator (or a curious reader) that answers what used
to need four curls: are the services up, which sessions are missing and why,
how many rows each dataset holds, and how recent the newest result is. Every
part is optional, so a down service shows as down rather than breaking the
page that is meant to diagnose it.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.routers.dashboard import _clients, status as service_status
from app.services.downstream import gather_optional

router = APIRouter(tags=["health"])

#: Enough to act on; the full list is one curl away at /ingest/gaps.
GAPS_SHOWN = 25


@router.get("/data-health", response_model=Dict[str, Any])
async def data_health(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    clients = _clients(settings)
    ingestion = clients["ingestion"]
    season = datetime.now(timezone.utc).year
    fetched = await gather_optional(
        services=service_status(settings),
        training=ingestion.get("/ingest/status", {"depth": "results"}),
        laps=ingestion.get("/ingest/status", {"depth": "full", "from_season": 2018}),
        gaps=ingestion.get("/ingest/gaps"),
        datasets=ingestion.get("/data/datasets"),
        latest=ingestion.get("/data/results", {"season": season, "limit": 1}),
        next_weekend=ingestion.get("/forward/next"),
    )

    gaps = fetched.get("gaps") or []
    latest = (fetched.get("latest") or [None])[0]
    services = fetched.get("services")
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": services.model_dump() if services is not None else None,
        "completeness": {
            "training": (fetched.get("training") or {}).get("summary"),
            "lap_data": (fetched.get("laps") or {}).get("summary"),
        },
        "gaps": [
            {
                "session": "{}-{}".format(g.get("season"), g.get("round")),
                "state": g.get("state"),
                "depth": g.get("depth"),
                "reason": g.get("reason", ""),
                "attempts": g.get("attempts", 0),
                "retry_after": g.get("retry_after"),
            }
            for g in gaps[:GAPS_SHOWN]
        ],
        "gaps_total": len(gaps),
        "datasets": fetched.get("datasets"),
        "latest_result": {
            "season": latest.get("season"), "round": latest.get("round"),
            "race_name": latest.get("race_name", ""), "race_date": latest.get("race_date", ""),
        } if latest else None,
        "next_race": {
            k: (fetched.get("next_weekend") or {}).get(k)
            for k in ("season", "round", "race_name", "race_start_utc")
        } if fetched.get("next_weekend") else None,
        "unavailable": fetched["_unavailable"],
    }
