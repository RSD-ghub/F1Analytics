"""Clients for the three internal services.

core-api is the only publicly reachable service; these are its only way to
reach the others. Two behaviours matter:

**Downstream failure is 503, never 500.** "prediction-service is unreachable"
is a different fact from "core-api is broken", and collapsing them sends
whoever is on call to the wrong service.

**Partial answers are allowed.** A page showing a forecast plus a track record
should still render the forecast when scoring is down, with the missing piece
marked absent. Failing the whole page because one panel is unavailable makes an
outage larger than it is.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class DownstreamUnavailable(RuntimeError):
    def __init__(self, service: str, detail: str) -> None:
        super().__init__("{} unavailable: {}".format(service, detail))
        self.service = service
        self.detail = detail


class ServiceClient:
    def __init__(self, name: str, base_url: str, timeout_seconds: float = 10.0) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = "{}{}".format(self._base_url, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise DownstreamUnavailable(
                self.name, "{} returned {}".format(path, exc.response.status_code)
            ) from exc
        except httpx.HTTPError as exc:
            raise DownstreamUnavailable(self.name, str(exc)) from exc

    async def post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = "{}{}".format(self._base_url, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload or {})
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise DownstreamUnavailable(
                self.name, "{} returned {}".format(path, exc.response.status_code)
            ) from exc
        except httpx.HTTPError as exc:
            raise DownstreamUnavailable(self.name, str(exc)) from exc

    async def healthy(self) -> bool:
        try:
            payload = await self.get("/health")
            return str(payload.get("status", "")).lower() in ("ok", "degraded")
        except DownstreamUnavailable:
            return False


async def gather_optional(**calls) -> Dict[str, Any]:
    """Run several downstream calls, tolerating individual failures.

    Returns ``{name: value_or_None}`` plus an ``_unavailable`` list naming what
    failed, so a caller can render what it has and be explicit about what it
    could not get — rather than silently showing a page with a missing panel
    and no explanation.
    """
    names = list(calls)
    settled = await asyncio.gather(*calls.values(), return_exceptions=True)

    out: Dict[str, Any] = {}
    unavailable: List[str] = []
    for name, value in zip(names, settled):
        if isinstance(value, Exception):
            detail = getattr(value, "detail", str(value))
            logger.warning("optional call %s failed: %s", name, detail)
            out[name] = None
            unavailable.append(name)
        else:
            out[name] = value
    out["_unavailable"] = unavailable
    return out
