"""F1TV connection for FastF1. Internal: only core-api calls this.

core-api checks that the caller is the site's owner before forwarding, and
this service is not exposed outside the compose network (see Caddyfile).
"""

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import f1tv_auth

router = APIRouter(prefix="/f1tv", tags=["f1tv"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)

    def __repr__(self) -> str:  # never let a password reach a log line
        return "Credentials(username={!r}, password=***)".format(self.username)

    __str__ = __repr__


@router.get("/status", response_model=Dict[str, Any])
async def status() -> Dict[str, Any]:
    return await asyncio.to_thread(f1tv_auth.status)


@router.post("/connect", response_model=Dict[str, Any])
async def connect(credentials: Credentials) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            f1tv_auth.connect, credentials.username, credentials.password
        )
    except f1tv_auth.F1TVUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except f1tv_auth.F1TVSignInFailed as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.post("/disconnect", response_model=Dict[str, Any])
async def disconnect() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(f1tv_auth.disconnect)
    except f1tv_auth.F1TVUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc))
