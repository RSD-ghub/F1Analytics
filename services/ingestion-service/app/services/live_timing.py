"""F1's live timing feed, turned into the rows the rest of the platform reads.

FastF1's live client (``python -m fastf1.livetiming save FILE``) writes every
message of the SignalR feed to a text file, one per line, as it arrives. This
module reads those lines and keeps the race as it stands: lap rows, pit stops
and race-control messages in the same shape ingestion stores after a race,
so the prediction service's ``live_state.race_state`` reads a live race
exactly as it reads a finished one.

**Lines.** Two shapes. The first lines are the subscription's answer, the
full state of each topic: ``['TimingData', '{...json...}', '']``. After that
each line is one update, ``['TimingData', {...}, '2026-11-22T06:01:02.3Z']``,
written with Python's ``str``, so quotes are single and booleans are
``True``. ``ast.literal_eval`` reads both safely; FastF1's own JSON repair is
the fallback. Compressed topics (``CarData.z``, ``Position.z``) are skipped:
car telemetry is not needed and needs the F1TV token.

**Updates are diffs.** A topic's update only names what changed, and a list
in the full state (stints, messages) is updated as a dict keyed by index.
``merge`` folds them in; lists are kept as dicts with string keys.

**Laps.** ``TimingData.Lines[car].NumberOfLaps`` going up is a lap completed.
Its time is ``LastLapTime`` from the same update; when the time comes in a
later update instead, it fills the lap then. Position, compound and stint are
read at the moment the lap completes. A lap the feed skipped is kept with no
time, which ``race_state`` already fills with the field's median.

**Who is out.** ``Retired`` or ``Stopped`` in TimingData. The finished-race
code needs a status per car to tell a car that stopped from one that is laps
down; live, the timing screen says so directly, and that is what is passed on.
"""

import ast
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: Topics read here; everything else in the feed is ignored.
TOPICS = ("DriverList", "TimingData", "TimingAppData", "RaceControlMessages",
          "LapCount", "SessionInfo", "SessionStatus", "TrackStatus", "WeatherData",
          "Heartbeat")
#: SessionStatus values after which the race is over.
FINISHED = ("Finished", "Finalised", "Ends")
TRACK_STATUS = {"1": "", "2": "YELLOW", "4": "SC", "5": "RED", "6": "VSC", "7": "VSC"}


def parse_line(line: str) -> Optional[Tuple[str, Any, str]]:
    """``(topic, data, timestamp)`` from one recorded line, or None."""
    line = line.strip()
    if not line or not line.startswith("["):
        return None
    try:
        value = ast.literal_eval(line)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        fixed = line.replace("'", '"').replace("True", "true").replace("False", "false")
        try:
            value = json.loads(fixed)
        except ValueError:
            return None
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    topic, data = value[0], value[1]
    stamp = value[2] if len(value) > 2 else ""
    if not isinstance(topic, str) or topic.endswith(".z"):
        return None
    if isinstance(data, str):
        # The subscription's answer carries each topic as a JSON string.
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return topic, data, stamp if isinstance(stamp, str) else ""


def _as_dict(value: Any) -> Any:
    if isinstance(value, list):
        return {str(i): _as_dict(v) for i, v in enumerate(value)}
    if isinstance(value, dict):
        return {str(k): _as_dict(v) for k, v in value.items()}
    return value


def merge(base: Any, diff: Any) -> Any:
    """Fold one update into a topic's state. Lists become index-keyed dicts."""
    diff = _as_dict(diff)
    if not isinstance(diff, dict):
        return diff
    out = dict(base) if isinstance(base, dict) else {}
    for key, value in diff.items():
        if key == "_deleted":
            for gone in value.values() if isinstance(value, dict) else []:
                out.pop(str(gone), None)
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def lap_seconds(text: Any) -> float:
    """``"1:23.456"`` or ``"83.456"`` -> seconds; 0.0 when blank or unreadable."""
    if not isinstance(text, str) or not text.strip():
        return 0.0
    try:
        seconds = 0.0
        for part in text.strip().split(":"):
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return 0.0


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ordered(mapping: Any) -> List[Any]:
    if not isinstance(mapping, dict):
        return []
    return [mapping[k] for k in sorted(mapping, key=lambda k: _int(k, 10 ** 6))]


class LiveTiming:
    """The race so far, built one feed message at a time."""

    def __init__(self, season: int = 0, round_number: int = 0,
                 names: Optional[Mapping[int, str]] = None) -> None:
        self.season = season
        self.round = round_number
        #: Car number -> our spelling of the driver (the stored registry).
        self.names: Dict[int, str] = dict(names or {})
        self.state: Dict[str, Any] = {}
        #: car -> lap number -> lap row
        self.laps: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self.pit_stops: List[Dict[str, Any]] = []
        self._in_pit: Dict[str, bool] = {}
        self.messages = 0
        self.last_stamp = ""

    # ── reading ──────────────────────────────────────────────────────────

    def feed_lines(self, lines: Iterable[str]) -> int:
        count = 0
        for line in lines:
            parsed = parse_line(line)
            if parsed is not None:
                self.apply(*parsed)
                count += 1
        return count

    def apply(self, topic: str, data: Any, stamp: str = "") -> None:
        if topic not in TOPICS:
            return
        self.messages += 1
        if stamp:
            self.last_stamp = stamp
        if topic == "TimingData":
            self._timing(data)
        self.state[topic] = merge(self.state.get(topic), data)

    def _timing(self, diff: Any) -> None:
        """Laps and stops, read from a TimingData update before it is merged."""
        lines = _as_dict(diff).get("Lines") if isinstance(diff, (dict, list)) else None
        if not isinstance(lines, dict):
            return
        before = (self.state.get("TimingData") or {}).get("Lines") or {}
        for car, change in lines.items():
            if not isinstance(change, dict):
                continue
            now = merge(before.get(car) or {}, change)
            laps = self.laps.setdefault(car, {})
            done = _int(now.get("NumberOfLaps"), 0)
            last = change.get("LastLapTime")
            time_now = lap_seconds(last.get("Value")) if isinstance(last, dict) else 0.0
            recorded = max(laps, default=0)
            if done > recorded:
                stint, compound = self._stint(car)
                for number in range(recorded + 1, done + 1):
                    laps[number] = {
                        "lap": number,
                        # Only the lap just completed has its time in this update.
                        "lap_time_seconds": time_now if number == done else 0.0,
                        "position": _int(now.get("Position"), 0),
                        "compound": compound,
                        "stint": stint,
                    }
            elif time_now > 0 and done in laps and laps[done]["lap_time_seconds"] <= 0:
                laps[done]["lap_time_seconds"] = time_now
            in_pit = bool(now.get("InPit"))
            if in_pit and not self._in_pit.get(car, False) and done > 0:
                count = sum(1 for s in self.pit_stops if s["car"] == car)
                self.pit_stops.append({"car": car, "stop": count + 1, "lap": done + 1})
            self._in_pit[car] = in_pit

    def _stint(self, car: str) -> Tuple[int, str]:
        lines = (self.state.get("TimingAppData") or {}).get("Lines") or {}
        stints = _ordered((lines.get(car) or {}).get("Stints"))
        if not stints:
            return 0, ""
        compound = str((stints[-1] or {}).get("Compound") or "").upper()
        return len(stints), "" if compound in ("UNKNOWN", "TEST_UNKNOWN") else compound

    # ── what the race looks like now ────────────────────────────────────

    def driver_name(self, car: str) -> str:
        known = self.names.get(_int(car, -1))
        if known:
            return known
        entry = (self.state.get("DriverList") or {}).get(car) or {}
        full = entry.get("FullName") or " ".join(
            p for p in (entry.get("FirstName"), entry.get("LastName")) if p)
        return str(full or entry.get("Tla") or car).strip()

    def cars(self) -> List[str]:
        listed = [k for k, v in (self.state.get("DriverList") or {}).items()
                  if isinstance(v, dict) and k.isdigit()]
        timed = list((self.state.get("TimingData") or {}).get("Lines") or {})
        return sorted(set(listed) | set(timed), key=lambda k: _int(k))

    def lap(self) -> int:
        """Laps the leader has completed."""
        return max((max(v, default=0) for v in self.laps.values()), default=0)

    def total_laps(self) -> int:
        return _int((self.state.get("LapCount") or {}).get("TotalLaps"), 0)

    def status(self) -> str:
        return str((self.state.get("SessionStatus") or {}).get("Status") or "")

    def session(self) -> Dict[str, Any]:
        info = self.state.get("SessionInfo") or {}
        meeting = info.get("Meeting") or {}
        return {
            "name": info.get("Name") or "",
            "type": info.get("Type") or "",
            "meeting": meeting.get("Name") or "",
            "start": info.get("StartDate") or "",
            "path": info.get("Path") or "",
        }

    def finished(self) -> bool:
        return self.status() in FINISHED

    def retired(self) -> List[str]:
        lines = (self.state.get("TimingData") or {}).get("Lines") or {}
        return [self.driver_name(car) for car, row in lines.items()
                if isinstance(row, dict) and (row.get("Retired") or row.get("Stopped"))]

    def race_control(self) -> List[Dict[str, Any]]:
        messages = _ordered((self.state.get("RaceControlMessages") or {}).get("Messages"))
        return [{
            "season": self.season, "round": self.round,
            "category": str(m.get("Category") or ""),
            "message": str(m.get("Message") or ""),
            "lap": _int(m.get("Lap"), 0) or None,
        } for m in messages if isinstance(m, dict)]

    def summary(self) -> Dict[str, Any]:
        """Cheap to compute: what a poller checks before asking for rows."""
        track = (self.state.get("TrackStatus") or {}).get("Status")
        cars = self.cars()
        return {
            "season": self.season,
            "round": self.round,
            "session": self.session(),
            "status": self.status(),
            "finished": self.finished(),
            "lap": self.lap(),
            "total_laps": self.total_laps(),
            "track_status": TRACK_STATUS.get(str(track or ""), ""),
            "messages": self.messages,
            "last_message_at": self.last_stamp,
            "cars": len(cars),
            "unnamed_cars": [c for c in cars if _int(c, -1) not in self.names],
        }

    def snapshot(self) -> Dict[str, Any]:
        """The race so far as stored rows: laps, pit stops, race control."""
        laps = []
        for car, rows in self.laps.items():
            name = self.driver_name(car)
            for number in sorted(rows):
                laps.append(dict(rows[number], season=self.season, round=self.round,
                                 driver=name))
        stops = [{"season": self.season, "round": self.round,
                  "driver": self.driver_name(s["car"]), "stop": s["stop"], "lap": s["lap"]}
                 for s in self.pit_stops]
        return dict(self.summary(),
                    taken_at=datetime.now(timezone.utc).isoformat(),
                    laps=laps, pit_stops=stops, race_control=self.race_control(),
                    retired=self.retired())
