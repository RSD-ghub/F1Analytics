"""Wire and storage models for ingestion-service.

The completeness models (``ExpectedSession``, ``SessionIngestState``,
``IntegrityReport``) are the heart of this service. The previous pipeline treated
a failed session as a ``[WARN]`` line on stdout and carried on, so a gap became
invisible the moment the process exited. Here every session the calendar says
should exist is written down first, and its ingest outcome is recorded against
it — which makes "what are we missing?" a query rather than an archaeology
exercise.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Completeness ─────────────────────────────────────────────────────────────


class IngestDepth(str, Enum):
    """How much of a session was ingested.

    Model training needs only classifications, which load in ~2s per session.
    A full ingest reconstructs laps, stints, pit stops, weather, race control
    and telemetry from timing data and takes ~30s — a decade of that is hours.

    Recording the depth is what keeps completeness honest across both. Without
    it a results-only backfill has to either lie (mark a session COMPLETE when
    its lap data was never fetched) or drown the gap list in noise (mark every
    modern session PARTIAL for laps nobody asked it to fetch). Depth lets
    "complete" mean "complete for what was requested".
    """

    RESULTS = "results"   # classification + qualifying only
    FULL = "full"         # every dataset the session offers


class SessionState(str, Enum):
    """Outcome of ingesting one race session."""

    PENDING = "pending"          # expected, never attempted
    COMPLETE = "complete"        # ingested and passed every integrity check
    PARTIAL = "partial"          # ingested but at least one integrity check failed
    FAILED = "failed"            # could not be ingested at all
    UNAVAILABLE = "unavailable"  # upstream has no data yet (future/cancelled race)


class ExpectedSession(BaseModel):
    """One race the published schedule says should exist.

    Written from the schedule *before* any ingest is attempted, so a session that
    can never be fetched still leaves a record behind.
    """

    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    race_date: str = ""  # ISO date, "YYYY-MM-DD"
    session_start_utc: Optional[datetime] = None

    @property
    def key(self) -> str:
        return f"{self.season}-{self.round}"


class IntegrityCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class IntegrityReport(BaseModel):
    checks: List[IntegrityCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> List[IntegrityCheck]:
        return [check for check in self.checks if not check.passed]


class SessionIngestState(BaseModel):
    """Per-session ingest outcome — the queryable record of completeness."""

    season: int
    round: int
    state: SessionState = SessionState.PENDING
    #: What was asked for. A session COMPLETE at ``results`` depth is whole for
    #: training but still missing lap data, and asking for ``full`` later will
    #: correctly re-ingest it.
    depth: IngestDepth = IngestDepth.FULL
    reason: str = ""
    attempts: int = 0
    last_attempt_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    row_counts: Dict[str, int] = Field(default_factory=dict)
    integrity: Optional[IntegrityReport] = None

    @property
    def key(self) -> str:
        return f"{self.season}-{self.round}"


class CompletenessSummary(BaseModel):
    """Answers 'is the dataset whole?' for a season range."""

    from_season: int
    to_season: int
    #: The depth this summary is judged against. "Complete" is meaningless
    #: without it — complete for training is not complete for analytics.
    depth: IngestDepth = IngestDepth.FULL
    expected: int
    complete: int
    partial: int
    failed: int
    pending: int
    unavailable: int
    open_gaps: List[str] = Field(default_factory=list)  # "2024-7" style keys

    @property
    def is_complete(self) -> bool:
        return not self.open_gaps


# ── Domain rows ──────────────────────────────────────────────────────────────
#
# Each row carries a deterministic ``id`` so re-ingesting the same session
# upserts in place rather than duplicating. That is what makes gap-healing safe
# to run repeatedly.


class RowBase(BaseModel):
    id: str
    season: int
    round: int
    race_name: str = ""


class ResultRow(RowBase):
    circuit: str = ""
    race_date: str = ""
    driver: str
    team: str = "Unknown"
    position: int = 999
    points: float = 0.0
    # Comes free with the race results, and grid-vs-finish is one of the
    # strongest available features. 0 means pit lane start or no grid slot.
    grid_position: int = 0
    # Raw FastF1 classification: a numeric string when officially classified,
    # otherwise a code — "R" retired, "D" disqualified, "W" withdrawn, "N" not
    # classified. Retirements still carry a `position` (the order they stopped
    # in), so position alone cannot distinguish a genuine 19th place from a
    # first-lap retirement. Storing this raw is what makes that possible.
    classified_position: str = ""
    # "Finished", "Lapped", "Retired", "Accident", "Engine", ...
    status: str = ""


class QualifyingRow(RowBase):
    """Qualifying classification — the grid, before the race exists.

    Ingested separately from the race because for an upcoming weekend it is the
    only session that has happened yet. Q1/Q2/Q3 times are kept rather than just
    the slot: the gap to pole carries pace information the ordinal does not.
    """

    driver: str
    team: str = "Unknown"
    #: Where they qualified — the classification, before any penalty is applied.
    position: int = 999
    #: Where they will actually start. These are different facts and conflating
    #: them is a real error: a driver who qualifies P2 with a ten-place power
    #: unit penalty starts P12. Grid position is one of the model's largest
    #: weights, so treating P12 as P2 is a materially wrong forecast.
    #:
    #: 0 means "not known yet". Qualifying classification is published the
    #: moment the session ends; the penalty-adjusted grid is confirmed later,
    #: so for a live weekend this is legitimately empty for a while.
    grid_position: int = 0
    q1_seconds: float = 0.0
    q2_seconds: float = 0.0
    q3_seconds: float = 0.0

    @property
    def has_confirmed_grid(self) -> bool:
        return self.grid_position > 0

    @property
    def effective_grid(self) -> int:
        """Best available starting position.

        Falls back to the qualifying classification, which is right far more
        often than not — but the caller must record that it fell back, because
        on a penalty weekend the fallback is wrong for the drivers who matter.
        """
        return self.grid_position if self.grid_position > 0 else self.position


class PracticePaceRow(RowBase):
    """One driver's pace in one practice session.

    Practice runs after upgrades are fitted and before qualifying, so this is
    the earliest objective measurement of whether a car changed — and it is
    available to both lock windows without leaking anything.
    """

    session_name: str = ""       # "Practice 1" | "Practice 2" | "Practice 3"
    driver: str
    best_lap_seconds: float = 0.0
    #: Median of sustained-stint laps — much harder to disguise than a headline
    #: lap, which teams vary with fuel and engine mode on purpose.
    long_run_seconds: float = 0.0
    long_run_laps: int = 0
    timed_laps: int = 0


class LapRow(RowBase):
    driver: str
    lap: int
    lap_time_seconds: float = 0.0
    compound: str = "Unknown"
    stint: int = 0


class StintRow(RowBase):
    driver: str
    stint: int
    compound: str = "Unknown"
    laps: int = 0


class PitStopRow(RowBase):
    driver: str
    stop: int
    lap: int
    duration_seconds: float = 0.0


class WeatherRow(RowBase):
    sample_time: str = ""
    air_temp: float = 0.0
    track_temp: float = 0.0
    humidity: float = 0.0
    rainfall: bool = False


class RaceControlRow(RowBase):
    category: str = "Info"
    message: str = ""
    time: str = ""
    lap: int = 0


class TelemetryRow(RowBase):
    driver: str
    sample_time: str = ""
    max_speed: float = 0.0
    avg_speed: float = 0.0
    throttle_mean: float = 0.0
    brake_mean: float = 0.0


class RaceWeekend(BaseModel):
    """A weekend's full session timetable.

    ``ExpectedSession`` deliberately stays minimal — it is the completeness
    manifest key. This is the forward-looking view: which sessions run when, so
    lock windows can be placed relative to them and "has qualifying happened
    yet?" is answerable without guessing.
    """

    season: int
    round: int
    race_name: str = ""
    circuit: str = ""
    country: str = ""
    #: Session name (as published, e.g. "Qualifying", "Sprint") → UTC start.
    sessions: Dict[str, datetime] = Field(default_factory=dict)
    race_start_utc: Optional[datetime] = None
    qualifying_start_utc: Optional[datetime] = None
    is_sprint_weekend: bool = False

    @property
    def key(self) -> str:
        return f"{self.season}-{self.round}"


class QualifyingFreshness(BaseModel):
    """Whether the grid for a given round is known yet.

    Drives the post-quali lock window: prediction-service must not fire a
    grid-aware forecast against a grid that has not been ingested.
    """

    season: int
    round: int
    qualifying_start_utc: Optional[datetime] = None
    has_run: bool = False        # by the clock
    has_data: bool = False       # actually ingested
    driver_count: int = 0
    ingested_at: Optional[datetime] = None

    @property
    def is_stale(self) -> bool:
        """Qualifying has happened but its data has not landed."""
        return self.has_run and not self.has_data


class SessionPayload(BaseModel):
    """Everything extracted from a single race session."""

    session: ExpectedSession
    results: List[ResultRow] = Field(default_factory=list)
    laps: List[LapRow] = Field(default_factory=list)
    stints: List[StintRow] = Field(default_factory=list)
    pit_stops: List[PitStopRow] = Field(default_factory=list)
    weather: List[WeatherRow] = Field(default_factory=list)
    race_control: List[RaceControlRow] = Field(default_factory=list)
    telemetry: List[TelemetryRow] = Field(default_factory=list)

    def row_counts(self) -> Dict[str, int]:
        return {
            "results": len(self.results),
            "laps": len(self.laps),
            "stints": len(self.stints),
            "pit_stops": len(self.pit_stops),
            "weather": len(self.weather),
            "race_control": len(self.race_control),
            "telemetry": len(self.telemetry),
        }
