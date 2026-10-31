"""Race-day weather from Open-Meteo, for people and for Bernie — not the model.

The forecast model has never been fed weather, and this does not change that:
a feature goes in only once a backtest shows it helps, and a ten-day rain
forecast was never part of any backtest. So the forecast is labelled where it
is shown, and Bernie is told the same, so that "40% chance of rain" never reads
as something the race probabilities already account for.

Returns ``None`` rather than raising whenever there is nothing honest to show:
no circuit location, a race more than ``HORIZON_DAYS`` out (beyond Open-Meteo's
range), a race already finished, or the API unreachable. The page treats a
missing forecast as "not available yet", never as "dry".
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

#: Open-Meteo forecasts 16 days ahead.
HORIZON_DAYS = 16
#: A Grand Prix is under two hours; the window either side catches a shower
#: arriving on the formation lap or the last stint.
WINDOW_BEFORE = timedelta(hours=1)
WINDOW_AFTER = timedelta(hours=3)
#: Weather forecasts update hourly at best; a page refreshing every minute on
#: race day should not cost a request each time.
CACHE_SECONDS = 30 * 60

NOT_USED_BY_MODEL = (
    "Weather is not an input to the race forecast; these probabilities "
    "do not account for it."
)

_cache: Dict[Tuple[float, float, str], Tuple[float, Optional[Dict[str, Any]]]] = {}


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def location_of(circuit: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    location = (circuit or {}).get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


def summarise(payload: Dict[str, Any], start: datetime) -> Optional[Dict[str, Any]]:
    """Cut Open-Meteo's hourly arrays down to the race window."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    chance = hourly.get("precipitation_probability") or []
    rain = hourly.get("precipitation") or []
    temp = hourly.get("temperature_2m") or []

    first, last = start - WINDOW_BEFORE, start + WINDOW_AFTER
    hours: List[Dict[str, Any]] = []
    for i, stamp in enumerate(times):
        moment = _parse(stamp)
        if moment is None or not (first <= moment <= last):
            continue
        hours.append({
            "time_utc": moment.isoformat(),
            "rain_chance_pct": chance[i] if i < len(chance) else None,
            "rain_mm": rain[i] if i < len(rain) else None,
            "temperature_c": temp[i] if i < len(temp) else None,
        })
    if not hours:
        return None

    chances = [h["rain_chance_pct"] for h in hours if h["rain_chance_pct"] is not None]
    totals = [h["rain_mm"] for h in hours if h["rain_mm"] is not None]
    temps = [h["temperature_c"] for h in hours if h["temperature_c"] is not None]
    peak = max(chances) if chances else None
    return {
        "source": "Open-Meteo",
        "race_start_utc": start.isoformat(),
        "hours": hours,
        "rain_chance_max_pct": peak,
        "rain_total_mm": round(sum(totals), 1) if totals else None,
        "temperature_range_c": [min(temps), max(temps)] if temps else None,
        "summary": describe(peak, sum(totals) if totals else None),
        "note": NOT_USED_BY_MODEL,
    }


def describe(peak: Optional[float], total_mm: Optional[float]) -> str:
    if peak is None:
        return "No rain forecast available for the race window."
    if peak < 20:
        return "Dry race expected ({}% peak chance of rain).".format(round(peak))
    if peak < 50:
        return "Some chance of rain ({}% at its highest).".format(round(peak))
    wet = " with about {} mm forecast".format(round(total_mm, 1)) if total_mm else ""
    return "Rain likely ({}% at its highest{}).".format(round(peak), wet)


async def race_forecast(
    circuit: Optional[Dict[str, Any]],
    race_start_utc: Any,
    *,
    url: str,
    timeout: float = 5.0,
    now: Optional[datetime] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    """The race-window forecast, or ``None`` when there is nothing to show."""
    if not url:
        return None
    where = location_of(circuit)
    start = _parse(race_start_utc)
    if where is None or start is None:
        return None
    now = now or datetime.now(timezone.utc)
    if start + WINDOW_AFTER < now or start - now > timedelta(days=HORIZON_DAYS):
        return None

    key = (round(where[0], 3), round(where[1], 3), start.isoformat())
    held = _cache.get(key)
    if held and time.monotonic() - held[0] < CACHE_SECONDS:
        return held[1]

    params = {
        "latitude": where[0],
        "longitude": where[1],
        "hourly": "precipitation_probability,precipitation,temperature_2m",
        "timezone": "UTC",
        "start_date": (start - WINDOW_BEFORE).date().isoformat(),
        "end_date": (start + WINDOW_AFTER).date().isoformat(),
    }
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as fresh:
                response = await fresh.get(url, params=params)
        else:
            response = await client.get(url, params=params)
        response.raise_for_status()
        result = summarise(response.json(), start)
    except (httpx.HTTPError, ValueError) as exc:
        # Not cached: the next refresh should try again.
        log.warning("race weather unavailable: %s", exc)
        return None

    _cache[key] = (time.monotonic(), result)
    return result


def weather_facts(forecast: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bernie's view: one summary line and the caveat, never the raw hours."""
    if not forecast:
        return {}
    return {
        "race-day weather forecast (Open-Meteo)": forecast["summary"],
        "weather and the forecast model": NOT_USED_BY_MODEL,
    }
