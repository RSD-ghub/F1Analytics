"""Live forecasts. For now, replays of finished races (stage 3); the live feed
itself arrives with stage 4 and will serve frames of the same shape."""

import asyncio
import logging
from collections import OrderedDict
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_client, get_predictor
from app.services.ingestion_client import IngestionClient, IngestionUnavailable
from app.services.predictor import ConfirmedGridRequired, GridRequired, Predictor, ReplayUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live"])

#: Replays kept in memory. A replay is deterministic for its race and settings
#: and takes seconds to build, so the page can be reopened without the wait.
CACHE_SIZE = 16
_cache: "OrderedDict[Tuple, Dict]" = OrderedDict()
_locks: Dict[Tuple, asyncio.Lock] = {}


def clear_cache() -> None:
    _cache.clear()
    _locks.clear()


@router.get("/{season}/{round_number}/replay")
async def replay(
    season: int,
    round_number: int,
    circuit: str = Query("", description="Circuit name, as the weekend has it"),
    runs: int = Query(300, ge=50, le=2000),
    step: int = Query(1, ge=1, le=10),
    client: IngestionClient = Depends(get_client),
    predictor: Predictor = Depends(get_predictor),
) -> Dict:
    """A finished Grand Prix, forecast again at every ``step`` laps from what
    was known at that lap only. 404 when the race has no lap timing or no
    result, 503 when ingestion is down."""
    if not circuit:
        try:
            weekend = next((w for w in await client.weekends(season)
                            if w.round == round_number), None)
        except IngestionUnavailable:
            weekend = None
        if weekend is not None:
            circuit = weekend.circuit
    key = (season, round_number, circuit, runs, step)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if key in _cache:
            return _cache[key]
        try:
            result = await predictor.replay(season, round_number, circuit=circuit,
                                            runs=runs, step=step)
        except (ReplayUnavailable, GridRequired, ConfirmedGridRequired, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except IngestionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    _locks.pop(key, None)
    return result
