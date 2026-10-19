"""Race laps before 2018, from jolpica (the Ergast successor).

FastF1's lap timing starts in 2018. Ergast's lap-by-lap timing goes back to
1996, so for older seasons it is the only source there is — and the race-pace
estimate wants a decade of laps, not seven seasons.

**What it has and what it lacks.** Every driver's time and position on every
lap. Pit stops (lap and pit-lane duration) from 2012. No tyre compounds at all,
no sector times, no speed traps. Rows are written in the same shape as FastF1's
with those fields at their "absent" values — compound ``"Unknown"``, speeds and
sectors 0.0 — and ``stint`` counted from the pit stops where they exist, 0
(unknown) where they do not. Readers already treat all of those as absent.

**Same ids as FastF1's rows** (season, round, driver, lap), so if FastF1 ever
serves these seasons the richer rows replace these in place rather than sitting
beside them.

**Driver identity.** Timings carry Ergast's ``driverId``; the season's driver
list maps it to the same "Given Family" name the stored results use (checked
against 2010: identical, accents included). A lap whose driver cannot be mapped
is dropped rather than stored under an id nobody else uses.

**Rate limits.** jolpica allows a few requests a second and a few hundred an
hour. A race is ~10 pages of laps, so a decade is ~2,000 requests: hours, not
minutes. The client paces itself and backs off on 429, and the backfill skips
any race that already has laps, so it can be stopped and resumed freely.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from app.models.schemas import LapRow, PitStopRow
from app.services.jolpica import _seconds
from app.services.transforms import stable_id

logger = logging.getLogger(__name__)

#: The first season with pit-stop data in Ergast.
FIRST_PIT_STOP_SEASON = 2012
#: FastF1 lap timing from here on; the backfill never touches these seasons.
FIRST_FASTF1_LAP_SEASON = 2018
PAGE = 100


class JolpicaClient:
    """Paced GETs against jolpica. ``interval`` seconds between requests."""

    def __init__(self, base_url: str = "https://api.jolpi.ca/ergast",
                 interval: float = 7.5, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._interval = interval
        self._timeout = timeout
        self._last = 0.0
        self.requests = 0

    async def get(self, path: str, **params) -> dict:
        for attempt in range(6):
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as c:
                    response = await c.get("{}/{}".format(self._base, path), params=params)
            except httpx.HTTPError as exc:
                logger.warning("jolpica %s failed (%s); retrying", path, exc)
                await asyncio.sleep(30 * (attempt + 1))
                continue
            if response.status_code == 429:
                pause = float(response.headers.get("Retry-After") or 60 * (attempt + 1))
                logger.warning("jolpica rate limit; pausing %.0fs", pause)
                await asyncio.sleep(pause)
                continue
            response.raise_for_status()
            return response.json()["MRData"]
        raise RuntimeError("jolpica: gave up on {}".format(path))


async def driver_names(client: JolpicaClient, season: int) -> Dict[str, str]:
    data = await client.get("f1/{}/drivers.json".format(season), limit=PAGE)
    return {
        d["driverId"]: " ".join(p for p in (d.get("givenName"), d.get("familyName")) if p)
        for d in data["DriverTable"]["Drivers"]
    }


async def race_timings(client: JolpicaClient, season: int, round_number: int
                       ) -> Tuple[str, List[Tuple[str, int, float]]]:
    """(race name, [(driverId, lap, seconds)]) for one race, every page."""
    rows: List[Tuple[str, int, float]] = []
    name = ""
    offset, total = 0, None
    while total is None or offset < total:
        data = await client.get("f1/{}/{}/laps.json".format(season, round_number),
                                limit=PAGE, offset=offset)
        total = int(data.get("total", 0))
        for race in data["RaceTable"]["Races"]:
            name = race.get("raceName", name)
            for lap in race.get("Laps", []):
                number = int(lap["number"])
                for timing in lap.get("Timings", []):
                    rows.append((timing["driverId"], number, _seconds(timing.get("time"))))
        offset += PAGE
        if total == 0:
            break
    return name, rows


async def race_pit_stops(client: JolpicaClient, season: int, round_number: int
                         ) -> List[Tuple[str, int, int, float]]:
    """[(driverId, stop, lap, pit-lane seconds)]; empty before 2012."""
    if season < FIRST_PIT_STOP_SEASON:
        return []
    data = await client.get("f1/{}/{}/pitstops.json".format(season, round_number), limit=PAGE)
    out = []
    for race in data["RaceTable"]["Races"]:
        for stop in race.get("PitStops", []):
            duration = stop.get("duration") or ""
            try:
                seconds = float(duration) if ":" not in duration else _seconds(duration)
            except ValueError:
                seconds = 0.0
            out.append((stop["driverId"], int(stop["stop"]), int(stop["lap"]), seconds))
    return out


def build_rows(season: int, round_number: int, race_name: str,
               timings: Sequence[Tuple[str, int, float]],
               stops: Sequence[Tuple[str, int, int, float]],
               names: Dict[str, str]) -> Tuple[List[LapRow], List[PitStopRow]]:
    """Lap and pit-stop rows in ingestion's shape. Stints from the stops."""
    stop_laps: Dict[str, List[int]] = {}
    for driver_id, _, lap, _ in stops:
        stop_laps.setdefault(driver_id, []).append(lap)
    has_stops = season >= FIRST_PIT_STOP_SEASON
    laps, pits = [], []
    for driver_id, number, seconds in timings:
        driver = names.get(driver_id)
        if not driver:
            continue
        # A stop on lap N ends stint k; the out-lap N+1 starts stint k+1.
        stint = 1 + sum(1 for lap in stop_laps.get(driver_id, []) if lap < number) if has_stops else 0
        laps.append(LapRow(
            id=stable_id(season, round_number, driver, number),
            season=season, round=round_number, race_name=race_name,
            driver=driver, lap=number, lap_time_seconds=seconds,
            compound="Unknown", stint=stint,
        ))
    for driver_id, stop, lap, seconds in stops:
        driver = names.get(driver_id)
        if driver:
            pits.append(PitStopRow(
                id=stable_id(season, round_number, driver, stop),
                season=season, round=round_number, race_name=race_name,
                driver=driver, stop=stop, lap=lap, duration_seconds=seconds,
            ))
    return laps, pits


async def backfill(store, client: JolpicaClient, first: int, last: int,
                   rounds: Optional[Dict[int, List[int]]] = None) -> Dict[str, int]:
    """Laps (and pit stops from 2012) for every race in ``first``..``last``.

    Only seasons before FastF1's lap timing. Races that already have laps are
    skipped, so a stopped run resumes where it left off. ``rounds`` limits the
    run to given rounds per season; by default every round with stored results.
    """
    summary = {"races": 0, "laps": 0, "pit_stops": 0, "skipped": 0, "unmapped": 0}
    for season in range(first, min(last, FIRST_FASTF1_LAP_SEASON - 1) + 1):
        wanted = (rounds or {}).get(season)
        if wanted is None:
            results = await store.find_rows("results", {"season": season}, limit=5000)
            wanted = sorted({row["round"] for row in results})
        names = None
        for round_number in wanted:
            if await store.count_matching("laps", {"season": season, "round": round_number}):
                summary["skipped"] += 1
                continue
            if names is None:
                names = await driver_names(client, season)
            race_name, timings = await race_timings(client, season, round_number)
            stops = await race_pit_stops(client, season, round_number)
            laps, pits = build_rows(season, round_number, race_name, timings, stops, names)
            summary["unmapped"] += len(timings) - len(laps)
            if not laps:
                logger.warning("%s-%s: no laps from jolpica", season, round_number)
                continue
            await store.save_session_rows("laps", season, round_number, laps)
            if pits:
                await store.save_session_rows("pit_stops", season, round_number, pits)
            summary["races"] += 1
            summary["laps"] += len(laps)
            summary["pit_stops"] += len(pits)
            logger.info("%s-%s %s: %s laps, %s stops (%s requests so far)",
                        season, round_number, race_name, len(laps), len(pits), client.requests)
    return summary
