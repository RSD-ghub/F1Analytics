"""HTTP boundary to ingestion-service.

prediction-service never touches the ingestion database directly. Reading another
service's Mongo would recreate the god-object the redesign removed, one layer up,
and would freeze ingestion's internal schema into this service's assumptions.

Local models are defined here rather than imported from ingestion-service: this
is the contract prediction-service depends on, and it should break loudly at the
boundary if ingestion changes shape, not silently produce wrong features.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IngestionUnavailable(RuntimeError):
    """ingestion-service could not be reached or returned an error."""


class RaceResult(BaseModel):
    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    driver: str
    team: str = "Unknown"
    position: int = 999
    points: float = 0.0
    grid_position: int = 0
    classified_position: str = ""
    status: str = ""

    @property
    def classified(self) -> bool:
        """Did this entry get an official finishing position?

        Retirements still carry a ``position`` — the order they stopped in — so
        a first-lap retirement arrives as "P19" and is indistinguishable from a
        genuine 19th place on that field alone. ``ClassifiedPosition`` is the
        column that actually separates them: numeric when classified, a letter
        code ("R", "D", "W", "N") when not.

        Falls back to the position range when the field is absent, so data
        ingested before this field existed still behaves sensibly.
        """
        if self.classified_position:
            return self.classified_position.isdigit()
        return 1 <= self.position <= 30

    @property
    def retired(self) -> bool:
        """Did the car fail to complete the race?

        Deliberately distinct from ``classified``. A driver who crashes on the
        last lap having covered 90% of the distance is *both* classified (P17 is
        their real result) and retired (their car broke). Pace features should
        count the P17; reliability features should count the retirement.
        """
        if self.status and self.status.strip().lower() == "retired":
            return True
        return not self.classified

    @property
    def finished(self) -> bool:
        """Whether this result is usable as a pace signal."""
        return self.classified


class QualiResult(BaseModel):
    """One driver's qualifying classification and segment times.

    Grid position throws away most of what qualifying measured: P3 by 0.05s and
    P3 by 0.9s are the same ordinal and completely different races. The times
    are what carry that.
    """

    season: int
    round: int
    driver: str
    team: str = "Unknown"
    position: int = 999
    q1_seconds: float = 0.0
    q2_seconds: float = 0.0
    q3_seconds: float = 0.0

    @property
    def best_seconds(self) -> float:
        """Fastest lap set across any segment. 0.0 means no time was set.

        A driver knocked out in Q1 has only a Q1 time; one who reached Q3
        usually improved through all three. Taking the minimum of whatever
        exists compares them on their best effort, which is what the grid does.
        """
        times = [t for t in (self.q1_seconds, self.q2_seconds, self.q3_seconds) if t > 0]
        return min(times) if times else 0.0


class PracticePace(BaseModel):
    """One driver's pace in one practice session."""

    season: int
    round: int
    session_name: str = ""
    driver: str
    best_lap_seconds: float = 0.0
    long_run_seconds: float = 0.0
    long_run_laps: int = 0


#: Grid provenances that state where a driver actually starts. Mirrors
#: ingestion's ``CONFIRMED_GRID_SOURCES``; duplicated rather than imported
#: because the services share no runtime code, only a wire contract.
CONFIRMED_GRID_SOURCES = ("race_result", "official_final", "official_provisional")


class GridSlot(BaseModel):
    """Where a driver starts, and whether we actually know that yet."""

    season: int
    round: int
    driver: str
    team: str = "Unknown"
    #: Qualifying classification — pre-penalty.
    position: int = 999
    #: Confirmed starting position. 0 means the official grid is not published.
    grid_position: int = 0
    #: Where ``grid_position`` came from — see ingestion's ``GridSource``. The
    #: default is the honest one: absent provenance means unconfirmed.
    grid_source: str = "qualifying"
    #: Required to start from the pit lane. Already reflected in
    #: ``grid_position`` (the slots behind the last grid place), and carried
    #: separately so it can be shown to a reader.
    starts_from_pit_lane: bool = False

    @property
    def confirmed(self) -> bool:
        """True only when a source actually stated the starting order.

        Not merely ``grid_position > 0``. A position copied from the qualifying
        classification is a number in the same field meaning something quite
        different, and treating it as confirmed is precisely the conflation that
        put penalised drivers on the wrong slot.
        """
        return self.grid_position > 0 and self.grid_source in CONFIRMED_GRID_SOURCES

    @property
    def effective(self) -> int:
        """Start position to model with.

        Falling back to the qualifying classification is right most weekends and
        wrong exactly when it matters most — a ten-place penalty moves a
        front-runner into the midfield. The fallback is therefore always paired
        with a data-quality flag rather than used silently.
        """
        return self.grid_position if self.grid_position > 0 else self.position


class Weekend(BaseModel):
    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    sessions: Dict[str, datetime] = Field(default_factory=dict)
    race_start_utc: Optional[datetime] = None
    qualifying_start_utc: Optional[datetime] = None
    is_sprint_weekend: bool = False


class CompletenessStatus(BaseModel):
    is_complete: bool = True
    open_gaps: List[str] = Field(default_factory=list)


class IngestionClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = "{}{}".format(self._base_url, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise IngestionUnavailable("GET {} failed: {}".format(path, exc)) from exc

    async def season_results(self, season: int) -> List[RaceResult]:
        """Every ingested race result for a season.

        Unbounded by design — a season is at most ~500 rows, and the feature
        builder needs the whole thing to compute form windows. The point-in-time
        filtering happens locally in the feature builder, not here, so that the
        filter and the leakage test look at the same code path.
        """
        payload = await self._get(
            "/data/results", {"season": season, "limit": 20000}
        )
        return [RaceResult(**row) for row in payload]

    async def season_qualifying(self, season: int) -> List["QualiResult"]:
        """Every qualifying classification for a season, with segment times."""
        payload = await self._get(
            "/data/qualifying", {"season": season, "limit": 20000}
        )
        return [QualiResult(**row) for row in payload]

    async def season_practice(self, season: int) -> List["PracticePace"]:
        payload = await self._get("/data/practice", {"season": season, "limit": 20000})
        return [PracticePace(**row) for row in payload]

    async def grid(self, season: int, round_number: int) -> List[GridSlot]:
        payload = await self._get(
            "/data/qualifying", {"season": season, "round": round_number, "limit": 100}
        )
        return [GridSlot(**row) for row in payload]

    async def weekends(self, season: int) -> List[Weekend]:
        payload = await self._get("/forward/upcoming", {"limit": 25})
        return [Weekend(**row) for row in payload]

    async def next_race(self) -> Optional[Weekend]:
        payload = await self._get("/forward/next")
        return Weekend(**payload) if payload else None

    async def completeness(
        self, from_season: int, to_season: int, depth: str = "full"
    ) -> CompletenessStatus:
        """Used to gate a lock window, stamp ``data_quality``, and gate training.

        ``depth`` selects which question is being asked: a corpus complete for
        training (results + qualifying) is not complete for lap-level analytics.
        """
        payload = await self._get(
            "/ingest/status",
            {"from_season": from_season, "to_season": to_season, "depth": depth},
        )
        summary = payload.get("summary", {})
        return CompletenessStatus(
            is_complete=bool(payload.get("is_complete", False)),
            open_gaps=list(summary.get("open_gaps", [])),
        )
