"""The live race feed. Internal: core-api forwards start and stop for the owner
only, and the prediction service reads status and snapshot.

* ``GET /live/status``: is a feed running, for which race, the leader's lap,
  the session status. Cheap; polled every few seconds.
* ``GET /live/snapshot``: the race so far as stored rows (laps, pit stops,
  race control, who is out). Fetched when the lap moves.
* ``POST /live/start``: record a race live, or replay a recording.
* ``POST /live/stop``.
* ``GET /live/recordings``: recordings that can be replayed.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import get_store
from app.services import live_feed
from app.services.storage import IngestionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live"])


class StartRequest(BaseModel):
    season: int = Field(ge=2018, le=2100)
    round: int = Field(ge=1, le=40)
    #: A file name from /live/recordings to replay instead of recording live.
    recording: str = Field("", max_length=200)
    #: Replay pace, times real time; 0 feeds the whole recording at once.
    speed: float = Field(0.0, ge=0.0, le=1000.0)


def _feed() -> live_feed.LiveFeed:
    return live_feed.get_feed(get_settings().live_recordings_dir)


async def shutdown() -> None:
    if live_feed._feed is not None:
        await live_feed._feed.stop()


@router.get("/status", response_model=Dict[str, Any])
async def status() -> Dict[str, Any]:
    return _feed().status()


@router.get("/snapshot", response_model=Dict[str, Any])
async def snapshot() -> Dict[str, Any]:
    try:
        return _feed().snapshot()
    except live_feed.LiveFeedError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/recordings", response_model=Dict[str, Any])
async def recordings() -> Dict[str, Any]:
    return {"recordings": _feed().recordings()}


@router.post("/start", response_model=Dict[str, Any])
async def start(request: StartRequest,
                store: IngestionStore = Depends(get_store)) -> Dict[str, Any]:
    try:
        registry = await store.recall_drivers(request.season)
    except Exception:
        logger.warning("driver registry unavailable for %s", request.season, exc_info=True)
        registry = {}
    names = {number: entry[0] for number, entry in registry.items()}
    try:
        return await _feed().start(request.season, request.round, names,
                                   recording=request.recording, speed=request.speed)
    except live_feed.LiveFeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=503,
                            detail="could not start the recorder: {}".format(exc))


@router.post("/stop", response_model=Dict[str, Any])
async def stop() -> Dict[str, Any]:
    return await _feed().stop()
