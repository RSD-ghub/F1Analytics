"""The FastF1 boundary — the only module that touches the FastF1 library.

Everything network- and library-shaped lives here so the rest of the pipeline can
be tested against synthetic frames. ``IngestRunner`` depends on the
``SessionSource`` shape rather than this class, so a fake can be substituted
wholesale.

The contract that matters: **this module raises, it never warns-and-continues.**
The original ``scripts/fastf1-ingest/ingest.py`` printed ``[WARN] … continue`` on
a failed schedule fetch or session load, which is precisely how a missing race
became invisible the moment the process exited. Here a failure is an exception
the caller must classify and record.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from app.models.schemas import (
    ExpectedSession,
    PracticePaceRow,
    QualifyingRow,
    RaceWeekend,
    ResultRow,
    TelemetryRow,
)
from app.services import practice_pace, transforms
from app.services.transforms import (
    build_driver_name_map,
    build_telemetry_row,
    safe_int,
    safe_text,
)

logger = logging.getLogger(__name__)


class SessionFetchError(RuntimeError):
    """Ingest failed for a reason that may succeed on retry (network, timeout)."""


class SessionUnavailableError(SessionFetchError):
    """Upstream genuinely has no data — a future or cancelled race.

    Distinct from ``SessionFetchError`` because retrying is pointless and it must
    not be reported as a gap in the historical record.
    """


@dataclass
class SessionFrames:
    """Raw frames for one race, before extraction into typed rows."""

    session: ExpectedSession
    results: pd.DataFrame = field(default_factory=pd.DataFrame)
    laps: pd.DataFrame = field(default_factory=pd.DataFrame)
    weather: pd.DataFrame = field(default_factory=pd.DataFrame)
    race_control: pd.DataFrame = field(default_factory=pd.DataFrame)
    telemetry: List[TelemetryRow] = field(default_factory=list)


def _as_frame(value: Any) -> pd.DataFrame:
    """Normalise FastF1's Optional[DataFrame] attributes to a real frame."""
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value
    return pd.DataFrame()


def _session_frame(session: Any, attribute: str) -> pd.DataFrame:
    """Read a FastF1 session attribute that may raise on access.

    ``getattr(session, "laps", None)`` looks defensive and is not: the default
    only suppresses ``AttributeError``, while these are *properties* that raise
    ``DataNotLoadedError`` from inside when ``load()`` failed to populate them.
    FastF1 can log a ``NoLapDataError`` internally and return from ``load()``
    normally, so the exception surfaces later, outside whatever try block
    wrapped the load — which is exactly how a backfill died mid-run.
    """
    try:
        return _as_frame(getattr(session, attribute, None))
    except Exception as exc:
        logger.debug("session.%s unavailable: %s", attribute, exc)
        return pd.DataFrame()


def _column_datetime(event: Any, column: str) -> Optional[datetime]:
    """Read one schedule column as a UTC datetime.

    FastF1 mixes naive and tz-aware timestamps across columns and seasons;
    normalising to UTC here keeps every downstream comparison honest.
    """
    raw = event.get(column) if hasattr(event, "get") else None
    if raw is None or pd.isna(raw):
        return None
    try:
        stamp = pd.Timestamp(raw)
    except Exception:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.to_pydatetime().astimezone(timezone.utc)


def _event_datetime(event: Any) -> Optional[datetime]:
    """UTC start of the race session, when the schedule provides one."""
    for column in ("Session5DateUtc", "EventDate"):
        found = _column_datetime(event, column)
        if found is not None:
            return found
    return None


def _named_session(
    sessions: Dict[str, datetime], candidates: Iterable[str]
) -> Optional[datetime]:
    """First matching session start, tried in preference order."""
    for name in candidates:
        if name in sessions:
            return sessions[name]
    return None


class FastF1Source:
    """Fetches schedules and race sessions from FastF1."""

    def __init__(self, cache_dir: str, load_telemetry: bool = True) -> None:
        self._cache_dir = cache_dir
        self._load_telemetry = load_telemetry
        self._cache_enabled = False

    # ── Library plumbing ─────────────────────────────────────────────────────

    def _fastf1(self):
        """Import FastF1 lazily and enable its on-disk cache exactly once.

        Deferred so importing this module (and running the transform tests)
        doesn't pull in the whole FastF1 stack.
        """
        import os

        import fastf1

        if not self._cache_enabled:
            os.makedirs(self._cache_dir, exist_ok=True)
            fastf1.Cache.enable_cache(self._cache_dir)
            self._cache_enabled = True
        return fastf1

    # ── Schedule ─────────────────────────────────────────────────────────────

    def fetch_schedule(self, season: int) -> List[ExpectedSession]:
        """Every race the published calendar says this season contains.

        This is what the completeness manifest is built from, so a failure here
        must propagate — silently returning ``[]`` would make an entire season
        look like it was never supposed to exist.
        """
        fastf1 = self._fastf1()
        try:
            schedule = fastf1.get_event_schedule(season, include_testing=False)
        except Exception as exc:
            raise SessionFetchError(
                "schedule fetch failed for season {}: {}".format(season, exc)
            ) from exc

        sessions: List[ExpectedSession] = []
        for _, event in schedule.iterrows():
            round_number = safe_int(event.get("RoundNumber"), 0)
            if round_number <= 0:
                continue  # pre-season testing carries round 0
            start = _event_datetime(event)
            sessions.append(
                ExpectedSession(
                    season=season,
                    round=round_number,
                    race_name=safe_text(
                        event.get("EventName"), "Round {}".format(round_number)
                    ),
                    circuit=safe_text(event.get("Location"), ""),
                    race_date=start.strftime("%Y-%m-%d") if start else "",
                    session_start_utc=start,
                )
            )

        # An empty schedule for a season that has already begun is a failed
        # fetch wearing the costume of a successful one. Upstream rate limiting
        # returns an empty frame rather than an error, and letting that through
        # is catastrophic for completeness: zero expected sessions means zero
        # gaps, so a season we never managed to fetch would be reported as
        # COMPLETE. Raising turns a silent hole into a retryable failure.
        if not sessions and season <= datetime.now(timezone.utc).year:
            raise SessionFetchError(
                "schedule for {} came back empty; treating as a failed fetch "
                "rather than a season with no races".format(season)
            )
        return sessions

    def fetch_weekends(self, season: int) -> List[RaceWeekend]:
        """Full session timetables for a season.

        Separate from ``fetch_schedule`` because the two answer different
        questions: the manifest asks "which races must exist?", this asks "when
        does each session run?". Keeping the manifest minimal stops the
        completeness key from drifting as the timetable shape changes.
        """
        fastf1 = self._fastf1()
        try:
            schedule = fastf1.get_event_schedule(season, include_testing=False)
        except Exception as exc:
            raise SessionFetchError(
                "schedule fetch failed for season {}: {}".format(season, exc)
            ) from exc

        weekends: List[RaceWeekend] = []
        for _, event in schedule.iterrows():
            round_number = safe_int(event.get("RoundNumber"), 0)
            if round_number <= 0:
                continue

            sessions: Dict[str, datetime] = {}
            for index in range(1, 6):
                name = safe_text(event.get("Session{}".format(index)))
                start = _column_datetime(event, "Session{}DateUtc".format(index))
                if name and start:
                    sessions[name] = start

            weekends.append(
                RaceWeekend(
                    season=season,
                    round=round_number,
                    race_name=safe_text(
                        event.get("EventName"), "Round {}".format(round_number)
                    ),
                    circuit=safe_text(event.get("Location"), ""),
                    country=safe_text(event.get("Country"), ""),
                    sessions=sessions,
                    race_start_utc=_named_session(sessions, ("Race",)),
                    qualifying_start_utc=_named_session(
                        sessions, ("Qualifying", "Sprint Qualifying", "Sprint Shootout")
                    ),
                    is_sprint_weekend=any(
                        "sprint" in name.lower() for name in sessions
                    ),
                )
            )
        return weekends

    # ── Session ──────────────────────────────────────────────────────────────

    def load_race(self, expected: ExpectedSession) -> SessionFrames:
        """Load one race session and return its raw frames.

        Raises ``SessionUnavailableError`` for a race that has not happened yet
        and ``SessionFetchError`` for anything else, so the runner can decide
        between "not a gap" and "retry, then record a gap".
        """
        if self._is_future(expected):
            raise SessionUnavailableError(
                "race has not run yet (scheduled {})".format(
                    expected.session_start_utc or expected.race_date or "unknown"
                )
            )

        fastf1 = self._fastf1()
        try:
            session = fastf1.get_session(expected.season, expected.round, "R")
            session.load(
                laps=True,
                telemetry=self._load_telemetry,
                weather=True,
                messages=True,
            )
        except Exception as exc:
            raise SessionFetchError(
                "session load failed for {}: {}".format(expected.key, exc)
            ) from exc

        results = _session_frame(session, "results")
        laps = _session_frame(session, "laps")

        frames = SessionFrames(
            session=self._with_actual_date(expected, session),
            results=results,
            laps=laps,
            weather=_session_frame(session, "weather_data"),
            race_control=_session_frame(session, "race_control_messages"),
        )
        if self._load_telemetry:
            frames.telemetry = self._extract_telemetry(frames.session, session, results)
        return frames

    def load_race_results(self, expected: ExpectedSession) -> List[ResultRow]:
        """Classification only — no laps, telemetry, weather or messages.

        Roughly two seconds per race against ~30 for a full load, because it is
        served from the results endpoint rather than reconstructed from timing
        data. That difference is what makes a decade of training data a
        coffee-break job instead of an overnight one.

        Not a substitute for ``load_race``: it deliberately returns none of the
        lap-level data the completeness guarantee is built on, so it is for
        assembling model training sets, not for filling the dataset.
        """
        fastf1 = self._fastf1()
        try:
            session = fastf1.get_session(expected.season, expected.round, "R")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
        except Exception as exc:
            raise SessionFetchError(
                "results load failed for {}: {}".format(expected.key, exc)
            ) from exc

        return transforms.extract_results(
            expected, _session_frame(session, "results")
        )

    def load_race_frames_results_only(
        self, expected: ExpectedSession
    ) -> SessionFrames:
        """Classification only, wrapped as ``SessionFrames``.

        Lets the runner treat a shallow ingest exactly like a deep one — same
        extraction, same integrity path, same state recording — with only the
        depth flag differing.
        """
        if self._is_future(expected):
            raise SessionUnavailableError(
                "race has not run yet (scheduled {})".format(
                    expected.session_start_utc or expected.race_date or "unknown"
                )
            )

        fastf1 = self._fastf1()
        try:
            session = fastf1.get_session(expected.season, expected.round, "R")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
        except Exception as exc:
            raise SessionFetchError(
                "results load failed for {}: {}".format(expected.key, exc)
            ) from exc

        results = _session_frame(session, "results")
        if results.empty:
            raise SessionFetchError(
                "results for {} came back empty; treating as a failed fetch "
                "rather than a race with no classification".format(expected.key)
            )
        return SessionFrames(
            session=self._with_actual_date(expected, session), results=results
        )

    #: FastF1 session codes for the practice sessions, in running order.
    PRACTICE_SESSIONS = ("FP1", "FP2", "FP3")

    def load_practice(
        self, expected: ExpectedSession, session_code: str
    ) -> List[PracticePaceRow]:
        """Long-run pace from one practice session.

        Needs lap data, so this is the expensive path (~30s vs ~2s). It is worth
        it: practice is the only place a car's post-upgrade pace is measurable
        before qualifying.

        A session that does not exist (a sprint weekend has no FP2/FP3) raises
        ``SessionUnavailableError`` so the caller can skip it without recording
        a gap — the session was never supposed to happen.
        """
        fastf1 = self._fastf1()
        try:
            session = fastf1.get_session(expected.season, expected.round, session_code)
        except ValueError as exc:
            # The session was never scheduled — a sprint weekend runs one
            # practice, not three. Permanently unavailable, so do not retry.
            raise SessionUnavailableError(
                "{} does not exist for {}".format(session_code, expected.key)
            ) from exc
        except Exception as exc:
            raise SessionFetchError(
                "could not open {} for {}: {}".format(
                    session_code, expected.key, str(exc)[:120]
                )
            ) from exc

        try:
            session.load(laps=True, telemetry=False, weather=False, messages=False)
            laps = _as_frame(session.laps)
        except Exception as exc:
            # Retryable. Deliberately *not* swallowed into an empty frame:
            # doing that turns a transient API failure into "this session has
            # no data", which is indistinguishable from a genuinely
            # practice-free weekend and silently thins the corpus.
            raise SessionFetchError(
                "{} load failed for {}: {}".format(
                    session_code, expected.key, str(exc)[:120]
                )
            ) from exc

        if laps.empty:
            raise SessionUnavailableError(
                "{} for {} has no laps".format(session_code, expected.key)
            )

        names = build_driver_name_map(_session_frame(session, "results"))
        return practice_pace.extract_practice_pace(
            expected, session_code, laps, names
        )

    def load_qualifying(self, expected: ExpectedSession) -> List[QualifyingRow]:
        """Load a qualifying session and return the grid.

        The forward-looking counterpart to ``load_race``: for an upcoming
        weekend this is the only session that has run, and it is what a
        post-quali forecast is conditioned on. Telemetry is skipped — the grid
        and segment times are all that is needed, and loading car data here would
        multiply the cost of a lock-window refresh for no gain.
        """
        fastf1 = self._fastf1()
        try:
            session = fastf1.get_session(expected.season, expected.round, "Q")
            session.load(laps=False, telemetry=False, weather=False, messages=False)
        except Exception as exc:
            raise SessionFetchError(
                "qualifying load failed for {}: {}".format(expected.key, exc)
            ) from exc

        rows = transforms.extract_qualifying(
            expected, _session_frame(session, "results")
        )
        # A qualifying session that ran but yields no classification is a failed
        # fetch, not an empty session — upstream returns an empty frame under
        # rate limiting rather than an error. Returning [] here reads as "no
        # grid yet", which is indistinguishable from a race that has not
        # qualified, so the caller would never retry and the gap would persist.
        if not rows:
            raise SessionFetchError(
                "qualifying for {} returned no classification; treating as a "
                "failed fetch rather than an empty session".format(expected.key)
            )
        return rows

    @staticmethod
    def _is_future(expected: ExpectedSession) -> bool:
        if expected.session_start_utc is None:
            return False
        start = expected.session_start_utc
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return start > datetime.now(timezone.utc)

    @staticmethod
    def _with_actual_date(expected: ExpectedSession, session: Any) -> ExpectedSession:
        """Prefer the session's own date over the schedule's, when it has one."""
        try:
            actual = getattr(session, "date", None)
            if actual is not None and not pd.isna(actual):
                return expected.model_copy(
                    update={"race_date": pd.Timestamp(actual).strftime("%Y-%m-%d")}
                )
        except Exception:
            pass
        return expected

    def _extract_telemetry(
        self,
        expected: ExpectedSession,
        session: Any,
        results: pd.DataFrame,
    ) -> List[TelemetryRow]:
        """Summarise each driver's fastest lap into one telemetry row.

        Telemetry is best-effort by design: it is a derived convenience summary,
        not a source of truth, and a driver with no valid fastest lap legitimately
        has none. Absence here is not a completeness gap — the integrity checks
        deliberately do not assert on it.
        """
        rows: List[TelemetryRow] = []
        driver_names = build_driver_name_map(results)
        drivers = getattr(session, "drivers", None) or []

        for driver in drivers:
            try:
                driver_laps = self._pick_driver(session.laps, driver)
                if driver_laps is None or driver_laps.empty:
                    continue
                fastest = driver_laps.pick_fastest()
                if fastest is None or fastest.empty:
                    continue

                car_data = fastest.get_car_data()
                if car_data is None or car_data.empty:
                    continue

                code = safe_text(fastest.get("Driver"), safe_text(driver, "Unknown"))
                rows.append(
                    build_telemetry_row(
                        session=expected,
                        driver=driver_names.get(code, code),
                        lap_time=safe_text(fastest.get("LapTime")),
                        speed=car_data.get("Speed") if "Speed" in car_data else None,
                        throttle=(
                            car_data.get("Throttle") if "Throttle" in car_data else None
                        ),
                        brake=car_data.get("Brake") if "Brake" in car_data else None,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "telemetry skipped for %s driver %s: %s", expected.key, driver, exc
                )
                continue
        return rows

    @staticmethod
    def _pick_driver(laps: Any, driver: str) -> Optional[pd.DataFrame]:
        """FastF1 renamed ``pick_driver`` to ``pick_drivers``; support both."""
        if hasattr(laps, "pick_drivers"):
            return laps.pick_drivers(driver)
        if hasattr(laps, "pick_driver"):
            return laps.pick_driver(driver)
        return None
