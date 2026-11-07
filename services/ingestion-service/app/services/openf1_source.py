"""Lap-level data from OpenF1, for races F1's live timing no longer serves.

In October 2026 Formula 1 put its live-timing archive behind an F1TV login.
FastF1 still loads results (it falls back to jolpica), but every lap, stint,
pit stop, weather sample and race-control message comes back empty, so from
the United States Grand Prix onwards the pit-stop review, the strategy pages
and practice pace had nothing to read.

OpenF1 (https://openf1.org) republishes the same timing data with no account
for every session since 2023. Checked against Singapore 2026, which both
sources still had: 1,240 laps, 47 stints and 25 pit stops on each side, and
sector times identical to the millisecond.

This module turns OpenF1's JSON into frames shaped like FastF1's, with the
same column names, so ``transforms`` and ``practice_pace`` read them without
knowing where they came from. It is only a fallback: anything FastF1 already
has, from its cache or a live load, is used as before.

Two things OpenF1 does differently, both handled here:

* It has no round numbers, and its 2026 list includes races our calendar does
  not. A session is matched on the date it ran, never on its place in a list.
* It names drivers in capitals ("Lando NORRIS"). Laps are keyed on the
  three-letter code, which the results frame maps to our own spelling, so
  OpenF1's names are only a last resort for a code nobody else knows.

The free tier allows 30 requests a minute; calls are spaced to stay under it.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.models.schemas import ExpectedSession

logger = logging.getLogger(__name__)

#: First season OpenF1 publishes.
FIRST_SEASON = 2023

#: 30 a minute is the published free limit; 2.1 s apart keeps under it.
MIN_INTERVAL_SECONDS = 2.1

#: How far a session's start may sit from the date our schedule gives. Wide
#: enough for a timezone and a late start; far narrower than the week between
#: two races, so it can never pick a neighbouring weekend.
MATCH_WINDOW = timedelta(hours=36)

#: How far before the race a practice session of the same weekend can be.
PRACTICE_WINDOW = timedelta(days=4)

SESSION_NAMES = {
    "R": "Race",
    "S": "Sprint",
    "FP1": "Practice 1",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
}


#: FastF1's spelling, so one weekend never carries two.
UNKNOWN_COMPOUND = "UNKNOWN"


def _stint_for(stints: List[Dict[str, Any]], lap_number: int) -> Optional[Dict[str, Any]]:
    """The stint a lap belongs to, or the nearest one when none covers it.

    OpenF1's stint ranges sometimes leave a lap uncovered (often the last
    lap, or the lap a car pitted on). Left alone, that lap became a
    one-lap "stint 0" on an unknown tyre and shortened the real stint by
    one; the car was on the tyre either side of it, so the nearest stint is
    the honest answer.
    """
    best, best_gap = None, None
    for stint in stints:
        first = int(stint.get("lap_start") or 0)
        last = int(stint.get("lap_end") or first)
        if first <= lap_number <= last:
            return stint
        gap = first - lap_number if lap_number < first else lap_number - last
        if best_gap is None or gap < best_gap:
            best, best_gap = stint, gap
    return best


class OpenF1Unavailable(RuntimeError):
    """OpenF1 could not answer, or has no such session."""


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _race_start(expected: ExpectedSession) -> Optional[datetime]:
    if expected.session_start_utc:
        start = expected.session_start_utc
        return start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    if expected.race_date:
        try:
            return datetime.fromisoformat(expected.race_date).replace(
                hour=12, tzinfo=timezone.utc
            )
        except ValueError:
            return None
    return None


def _seconds(value: Any) -> Any:
    """A duration in seconds as a Timedelta, or NaT when there is none."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return pd.NaT
    if number != number or number <= 0:
        return pd.NaT
    return pd.Timedelta(round(number * 1000), unit="ms")


def _speed(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


class OpenF1Client:
    """A small, polite client for the endpoints the ingest needs."""

    def __init__(self, base_url: str, timeout: float = 20.0,
                 min_interval: float = MIN_INTERVAL_SECONDS, http=None,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self._base = (base_url or "").rstrip("/")
        self._timeout = timeout
        self._min_interval = min_interval
        self._http = http
        self._clock = clock
        self._sleep = sleep
        self._last: Optional[float] = None
        self._lock = threading.Lock()
        self._sessions: Dict[int, List[Dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._base)

    def get(self, path: str, **params: Any) -> List[Dict[str, Any]]:
        if not self.enabled:
            raise OpenF1Unavailable("OpenF1 is turned off")
        with self._lock:
            if self._last is not None:
                wait = self._min_interval - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
        http = self._http
        if http is None:
            import httpx

            http = httpx
        try:
            response = http.get(
                "{}/{}".format(self._base, path.lstrip("/")),
                params=params,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        except Exception as exc:
            raise OpenF1Unavailable("{} failed: {}".format(path, exc)) from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise OpenF1Unavailable(
                "{} returned HTTP {}".format(path, response.status_code)
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise OpenF1Unavailable("{} returned no JSON".format(path)) from exc
        return body if isinstance(body, list) else []

    # ── Finding the session ─────────────────────────────────────────────────

    def _season_sessions(self, season: int) -> List[Dict[str, Any]]:
        if season not in self._sessions:
            self._sessions[season] = self.get("sessions", year=season)
        return self._sessions[season]

    def find_session(self, expected: ExpectedSession, code: str) -> Dict[str, Any]:
        """The OpenF1 session for one of our weekends, matched by date.

        The race is found first, by its start time. Practice and sprint
        sessions are then taken from the same meeting, so a weekend is never
        stitched together from two different events.
        """
        if expected.season < FIRST_SEASON:
            raise OpenF1Unavailable("OpenF1 starts in {}".format(FIRST_SEASON))
        name = SESSION_NAMES.get(code)
        if name is None:
            raise OpenF1Unavailable("no OpenF1 name for session {}".format(code))
        start = _race_start(expected)
        if start is None:
            raise OpenF1Unavailable("{} has no date to match on".format(expected.key))

        sessions = self._season_sessions(expected.season)
        races = [
            s for s in sessions
            if s.get("session_name") == "Race" and _parse_time(s.get("date_start"))
        ]
        race = min(
            races,
            key=lambda s: abs(_parse_time(s["date_start"]) - start),
            default=None,
        )
        if race is None or abs(_parse_time(race["date_start"]) - start) > MATCH_WINDOW:
            raise OpenF1Unavailable("no OpenF1 race near {}".format(start.isoformat()))
        if code == "R":
            return race

        for session in sessions:
            if (session.get("meeting_key") == race.get("meeting_key")
                    and session.get("session_name") == name):
                when = _parse_time(session.get("date_start"))
                if when and timedelta(0) <= start - when <= PRACTICE_WINDOW:
                    return session
        raise OpenF1Unavailable("{} has no {} on OpenF1".format(expected.key, name))

    # ── Frames ──────────────────────────────────────────────────────────────

    def _codes(self, session_key: int) -> Dict[int, Tuple[str, str]]:
        """car number -> (three-letter code, OpenF1's name in title case)."""
        out: Dict[int, Tuple[str, str]] = {}
        for row in self.get("drivers", session_key=session_key):
            number = row.get("driver_number")
            code = (row.get("name_acronym") or "").strip()
            if number is None or not code:
                continue
            first = (row.get("first_name") or "").strip()
            last = (row.get("last_name") or "").strip().title()
            full = " ".join(p for p in (first, last) if p) or code
            out[int(number)] = (code, full)
        return out

    def laps_frame(
        self, session_key: int, with_pits: bool
    ) -> Tuple[pd.DataFrame, Dict[str, str], Dict[str, int]]:
        """Laps in FastF1's shape, plus code -> name and code -> car number.

        ``PitInTime``/``PitOutTime`` follow FastF1's convention: entry on the
        in-lap, exit on the following lap, so ``extract_pit_stops`` pairs them
        unchanged. For a race they come from OpenF1's pit records, which carry
        the pit-lane time; for practice, where only "was this a pit lap"
        matters, they are inferred from the out-laps.
        """
        codes = self._codes(session_key)
        laps = self.get("laps", session_key=session_key)
        stints = self.get("stints", session_key=session_key)
        pits = self.get("pit", session_key=session_key) if with_pits else []

        stint_of: Dict[int, List[Dict[str, Any]]] = {}
        for stint in stints:
            stint_of.setdefault(int(stint.get("driver_number") or 0), []).append(stint)

        rows = []
        for lap in laps:
            number = int(lap.get("driver_number") or 0)
            if number not in codes:
                continue
            lap_number = int(lap.get("lap_number") or 0)
            compound, stint_number, tyre_life = UNKNOWN_COMPOUND, 0, float("nan")
            stint = _stint_for(stint_of.get(number, []), lap_number)
            if stint is not None:
                first = int(stint.get("lap_start") or 0)
                compound = (stint.get("compound") or UNKNOWN_COMPOUND).upper()
                stint_number = int(stint.get("stint_number") or 0)
                age = stint.get("tyre_age_at_start")
                if age is not None:
                    tyre_life = float(age) + max(lap_number - first, 0) + 1
            started = _parse_time(lap.get("date_start"))
            rows.append({
                "Driver": codes[number][0],
                "DriverNumber": str(number),
                "LapNumber": lap_number,
                "LapTime": _seconds(lap.get("lap_duration")),
                "Sector1Time": _seconds(lap.get("duration_sector_1")),
                "Sector2Time": _seconds(lap.get("duration_sector_2")),
                "Sector3Time": _seconds(lap.get("duration_sector_3")),
                "SpeedI1": _speed(lap.get("i1_speed")),
                "SpeedI2": _speed(lap.get("i2_speed")),
                "SpeedST": _speed(lap.get("st_speed")),
                "SpeedFL": float("nan"),
                "Stint": stint_number,
                "Compound": compound,
                "TyreLife": tyre_life,
                "IsPitOutLap": bool(lap.get("is_pit_out_lap")),
                "LapStartDate": pd.Timestamp(started) if started else pd.NaT,
                "PitInTime": pd.NaT,
                "PitOutTime": pd.NaT,
            })

        names = {code: full for code, full in codes.values()}
        numbers = {code: number for number, (code, _) in codes.items()}
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame, names, numbers
        frame = frame.sort_values(["Driver", "LapNumber"]).reset_index(drop=True)
        frame["PitInTime"] = pd.Series([pd.NaT] * len(frame), dtype="datetime64[ns, UTC]")
        frame["PitOutTime"] = pd.Series([pd.NaT] * len(frame), dtype="datetime64[ns, UTC]")
        index = {(row.Driver, row.LapNumber): i for i, row in frame.iterrows()}

        if with_pits:
            for pit in pits:
                number = int(pit.get("driver_number") or 0)
                if number not in codes:
                    continue
                code = codes[number][0]
                lap_number = int(pit.get("lap_number") or 0)
                duration = _seconds(pit.get("pit_duration") or pit.get("lane_duration"))
                entered = _parse_time(pit.get("date"))
                if pd.isna(duration) or entered is None:
                    continue
                in_row = index.get((code, lap_number))
                out_row = index.get((code, lap_number + 1))
                if in_row is None or out_row is None:
                    continue
                frame.at[in_row, "PitInTime"] = pd.Timestamp(entered)
                frame.at[out_row, "PitOutTime"] = pd.Timestamp(entered) + duration
        else:
            for i, row in frame[frame["IsPitOutLap"]].iterrows():
                marker = row["LapStartDate"]
                if pd.isna(marker):
                    marker = pd.Timestamp(0, tz="UTC")
                frame.at[i, "PitOutTime"] = marker
                before = index.get((row["Driver"], row["LapNumber"] - 1))
                if before is not None:
                    frame.at[before, "PitInTime"] = marker

        return frame.drop(columns=["IsPitOutLap", "LapStartDate"]), names, numbers

    def weather_frame(self, session_key: int) -> pd.DataFrame:
        rows = [
            {
                "Time": w.get("date") or "",
                "AirTemp": w.get("air_temperature"),
                "TrackTemp": w.get("track_temperature"),
                "Humidity": w.get("humidity"),
                "Rainfall": bool(w.get("rainfall")),
            }
            for w in self.get("weather", session_key=session_key)
        ]
        return pd.DataFrame(rows)

    def race_control_frame(self, session_key: int) -> pd.DataFrame:
        rows = [
            {
                "Time": m.get("date") or "",
                "Lap": m.get("lap_number") or 0,
                "Category": m.get("category") or "Info",
                "Message": m.get("message") or "",
            }
            for m in self.get("race_control", session_key=session_key)
        ]
        return pd.DataFrame(rows)
