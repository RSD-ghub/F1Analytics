"""Owner-only controls. Today: the F1TV account FastF1 signs in with.

Since October 2026 F1's live-timing archive needs an F1TV Access login, so
official laps, tyres, weather and race control only arrive while that
account is connected. The owner connects it here, from the Data health
page, instead of typing a password into a terminal.

The credentials pass straight through to ingestion-service, which signs in
once and keeps only the resulting token. Nothing here stores or logs them.
Every route checks ``ADMIN_EMAILS`` on the server; the frontend hiding the
form from everyone else is a courtesy, not the protection.
"""

from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import admin_user
from app.services import rate_limit as rl
from f1_common.observability import outgoing_headers

router = APIRouter(prefix="/admin", tags=["admin"])

#: A wrong password costs a round trip to F1's identity provider, and F1 is
#: entitled to lock an account that hammers it. Five tries an hour is plenty
#: for someone who mistyped.
CONNECT_ATTEMPTS = rl.Limit(attempts=5, window_seconds=60 * 60)

#: Signing in walks four requests through F1's identity provider.
SIGN_IN_TIMEOUT = 45.0


class F1TVCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)

    def __repr__(self) -> str:
        return "F1TVCredentials(username={!r}, password=***)".format(self.username)

    __str__ = __repr__


async def _ingestion(settings: Settings, method: str, path: str,
                     payload: Dict[str, Any] = None) -> Dict[str, Any]:
    url = "{}{}".format(settings.ingestion_service_url, path)
    try:
        async with httpx.AsyncClient(timeout=SIGN_IN_TIMEOUT) as client:
            response = await client.request(
                method, url, json=payload, headers=outgoing_headers()
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="ingestion-service is not reachable")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text
        except ValueError:
            detail = response.text
        # 401 from ingestion means F1 refused the sign-in. Passed on as 400:
        # a 401 here would read to the frontend as "your session expired"
        # and sign the owner out of the dashboard.
        status = 400 if response.status_code == 401 else response.status_code
        raise HTTPException(status_code=status, detail=detail)
    return response.json()


@router.get("/f1tv", response_model=Dict[str, Any])
async def f1tv_status(
    _=Depends(admin_user), settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    return await _ingestion(settings, "GET", "/f1tv/status")


@router.post("/f1tv/connect", response_model=Dict[str, Any])
async def f1tv_connect(
    credentials: F1TVCredentials,
    user=Depends(admin_user),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    key = "f1tv-connect:" + user["_id"]
    rl.limiter.check({key: CONNECT_ATTEMPTS})
    rl.limiter.hit(key)
    result = await _ingestion(settings, "POST", "/f1tv/connect", {
        "username": credentials.username, "password": credentials.password,
    })
    rl.limiter.reset(key)
    return result


@router.post("/f1tv/disconnect", response_model=Dict[str, Any])
async def f1tv_disconnect(
    _=Depends(admin_user), settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    return await _ingestion(settings, "POST", "/f1tv/disconnect")


# ── The live race feed ──────────────────────────────────────────────────────
#
# Starting the feed runs FastF1's live recorder on the machine ingestion runs
# on, so it is the owner's call, like F1TV. Viewing the live forecast is not:
# that is /live/now and /live/stream, open like the rest of the dashboard.


class LiveStart(BaseModel):
    season: int = Field(ge=2018, le=2100)
    round: int = Field(ge=1, le=40)
    #: Replay this recording instead of recording live (a name from GET).
    recording: str = Field("", max_length=200)
    speed: float = Field(0.0, ge=0.0, le=1000.0)


@router.get("/live", response_model=Dict[str, Any])
async def live_feed_status(
    _=Depends(admin_user), settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    status = await _ingestion(settings, "GET", "/live/status")
    recordings = await _ingestion(settings, "GET", "/live/recordings")
    return dict(status, recordings=recordings.get("recordings", []))


@router.post("/live/start", response_model=Dict[str, Any])
async def live_feed_start(
    request: LiveStart,
    _=Depends(admin_user),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    return await _ingestion(settings, "POST", "/live/start", request.model_dump())


@router.post("/live/stop", response_model=Dict[str, Any])
async def live_feed_stop(
    _=Depends(admin_user), settings: Settings = Depends(get_settings)
) -> Dict[str, Any]:
    return await _ingestion(settings, "POST", "/live/stop")
