"""The live forecast during a race: one frame per lap, from the live feed.

Ingestion's live feed (``ingestion-service/app/services/live_feed.py``) keeps
the race so far as stored-shape rows. This tracker polls its status every few
seconds and, each time the leader completes a lap, fetches the rows and makes
the forecast for that lap with ``live_replay.lap_frame``: the same function
the replay and the backtest use, so what the Live page shows during a race is
what was scored on 2024-25.

The field and its pre-race strengths are read once per race
(``Predictor.live_inputs``). Frames are kept in memory for the race; a viewer
asks for the frames after the last one it has.

**Who is out.** ``race_state`` tells a stopped car from a lapped one by its
final status. Live there is no final status, but the feed says who has
retired, so every other car is passed as still running.

It runs only while someone is looking: the loop starts on the first request
and stops after ``IDLE_STOP`` seconds without one.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services import live_replay
from app.services.ingestion_client import (
    IngestionClient, IngestionUnavailable, PitStop, RaceControlMessage, RaceLap, RaceResult,
)
from app.services.live_state import WET_COMPOUNDS

logger = logging.getLogger(__name__)

#: Seconds between polls of the feed's status.
POLL_SECONDS = 3.0
#: Simulated races per lap. As the replay.
RUNS = live_replay.REPLAY_RUNS
#: Stop polling this long after the last viewer asked, seconds.
IDLE_STOP = 15 * 60
#: A status the finished-race code reads as "took the flag": here, still running.
RUNNING = "Finished"


def live_results(season: int, round_number: int, drivers, retired) -> List[RaceResult]:
    """A status per car: retired as the feed says, everyone else running."""
    out = set(retired or [])
    return [RaceResult(season=season, round=round_number, driver=d.driver, team=d.team,
                       status="Retired" if d.driver in out else RUNNING)
            for d in drivers]


def rows_from(snapshot: Dict[str, Any]) -> Tuple[List[RaceLap], List[PitStop],
                                                  List[RaceControlMessage]]:
    laps = [RaceLap(**r) for r in snapshot.get("laps", [])]
    stops = [PitStop(**r) for r in snapshot.get("pit_stops", [])]
    messages = [RaceControlMessage(**r) for r in snapshot.get("race_control", [])]
    return laps, stops, messages


class LiveTracker:
    def __init__(self, client: IngestionClient, predictor, runs: int = RUNS) -> None:
        self._client = client
        self._predictor = predictor
        self.runs = runs
        self.race: Optional[Tuple[int, int]] = None
        self.race_name = ""
        self.inputs: Optional[Dict[str, Any]] = None
        self.frames: List[Dict[str, Any]] = []
        self.status: Dict[str, Any] = {}
        self.error = ""
        self.total_laps = 0
        self.wet = False
        self._wet_so_far = False
        self._task: Optional[asyncio.Task] = None
        self._asked = time.monotonic()

    # ── the loop ────────────────────────────────────────────────────────

    def touch(self) -> None:
        self._asked = time.monotonic()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        while time.monotonic() - self._asked < IDLE_STOP:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad lap must not end the race
                logger.exception("live tracker step failed")
                self.error = str(exc)
            await asyncio.sleep(POLL_SECONDS)

    async def step(self) -> None:
        """One poll: read the feed's status, and forecast any new laps."""
        try:
            status = await self._client.live_status()
        except IngestionUnavailable as exc:
            self.error = "the live feed is not reachable: {}".format(exc)
            return
        self.status = status
        season, round_number = int(status.get("season") or 0), int(status.get("round") or 0)
        if not season or not round_number:
            return
        if self.race != (season, round_number):
            await self._new_race(season, round_number)
        if self.inputs is None:
            return
        lap = int(status.get("lap") or 0)
        done = self.frames[-1]["lap"] if self.frames else -1
        if self.frames and lap <= done:
            return
        snapshot = await self._client.live_snapshot()
        laps, stops, messages = rows_from(snapshot)
        results = live_results(season, round_number, self.inputs["drivers"],
                               snapshot.get("retired"))
        self.total_laps = (int(snapshot.get("total_laps") or 0) or self.total_laps
                           or int(self.inputs["profile"].get("laps") or 0))
        self.wet = self.wet or any((r.compound or "").upper() in WET_COMPOUNDS for r in laps)
        lap = min(int(snapshot.get("lap") or 0), max(self.total_laps, 1))
        for number in range(done + 1, lap + 1):
            frame, self._wet_so_far = await asyncio.to_thread(
                self._frame, laps, stops, messages, results, number)
            self.frames.append(frame)
        self.error = ""

    async def _new_race(self, season: int, round_number: int) -> None:
        self.race = (season, round_number)
        self.frames, self.inputs, self.error = [], None, ""
        self.total_laps, self.wet, self._wet_so_far = 0, False, False
        self.race_name = ""
        circuit = ""
        try:
            weekend = next((w for w in await self._client.weekends(season)
                            if w.round == round_number), None)
            if weekend is not None:
                circuit, self.race_name = weekend.circuit, weekend.race_name
        except IngestionUnavailable:
            pass
        try:
            self.inputs = await self._predictor.live_inputs(season, round_number, circuit)
        except Exception as exc:
            logger.warning("live forecast unavailable for %s-%s: %s", season, round_number, exc)
            self.error = "no pre-race forecast to start from: {}".format(exc)
            # Retried on the next poll: the grid may simply not be in yet.
            self.race = None
            return
        self.total_laps = int(self.status.get("total_laps") or 0) or int(
            self.inputs["profile"].get("laps") or 0)

    def _frame(self, laps, stops, messages, results, lap: int):
        i = self.inputs
        return live_replay.lap_frame(
            i["drivers"], i["strengths"], i["profile"], i["params"], laps, stops, messages,
            results, lap, max(self.total_laps, lap + 1), i["seed"], self.runs,
            live_replay.REPLAY_PRIOR_LAPS, i["network"], self._wet_so_far, **i["retirement"])

    # ── what a viewer sees ──────────────────────────────────────────────

    def view(self, since: int = -1) -> Dict[str, Any]:
        status = self.status or {}
        season, round_number = self.race or (status.get("season"), status.get("round"))
        return {
            "live": bool(status.get("running")),
            "mode": status.get("mode", ""),
            "season": season,
            "round": round_number,
            "race_name": self.race_name or (status.get("session") or {}).get("meeting", ""),
            "session_status": status.get("status", ""),
            "finished": bool(status.get("finished")),
            "track_status": status.get("track_status", ""),
            "lap": status.get("lap", 0),
            "total_laps": self.total_laps or status.get("total_laps", 0),
            "wet": self.wet,
            "network": bool(self.inputs and self.inputs.get("network") is not None),
            "runs": self.runs,
            "unnamed_cars": status.get("unnamed_cars", []),
            "error": self.error or status.get("error", ""),
            "frames": [f for f in self.frames if f["lap"] > since],
        }


_tracker: Optional[LiveTracker] = None


def get_tracker(client: IngestionClient, predictor) -> LiveTracker:
    global _tracker
    if _tracker is None:
        _tracker = LiveTracker(client, predictor)
    return _tracker
