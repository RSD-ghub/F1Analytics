"""The live tracker: a race fed in lap by lap gives the frames a replay gives."""

import asyncio

import numpy as np
from fastapi.testclient import TestClient

from app import dependencies
from app.main import app
from app.services import live_replay, live_tracker
from app.services.ingestion_client import IngestionUnavailable

from tests.test_lap_simulation import PROFILE, params
from tests.test_live_forecast import DRIVERS, N, field, results, scripted_race

LAPS = scripted_race(pace=[-0.5, 0, 0, 0, 0, 0], retire=("D3", 12))


class Feed:
    """Ingestion's live feed, with the race so far up to ``lap``."""

    def __init__(self):
        self.lap = 0
        self.snapshots = 0

    async def live_status(self):
        return {"running": True, "mode": "live", "season": 2025, "round": 1,
                "lap": self.lap, "total_laps": 30, "status": "Started"}

    async def live_snapshot(self):
        self.snapshots += 1
        rows = [r.model_dump() for r in LAPS if r.lap <= self.lap]
        return {"lap": self.lap, "total_laps": 30, "laps": rows, "pit_stops": [],
                "race_control": [], "retired": ["D3"] if self.lap >= 13 else []}

    async def weekends(self, season):
        return []


class Predictor:
    async def live_inputs(self, season, round_number, circuit=""):
        return {"drivers": field(), "strengths": np.zeros(N), "profile": PROFILE,
                "params": params(), "seed": 1, "network": None,
                "retirement": dict(retirement_race_k=None, retirement_team_k=None)}


def run(tracker, feed, laps):
    """Advance the race until each lap in ``laps`` is ready to forecast: the
    leader one lap further on."""
    async def go():
        for lap in laps:
            feed.lap = lap + 1
            await tracker.step()
    asyncio.run(go())


def test_live_frames_match_the_replay_of_the_same_laps():
    feed = Feed()
    tracker = live_tracker.LiveTracker(feed, Predictor(), runs=200)
    run(tracker, feed, [0, 1, 2, 5, 5, 10, 20])
    assert [f["lap"] for f in tracker.frames] == list(range(21))
    assert feed.snapshots == 6          # not fetched again while the lap stands
    replay = live_replay.build_replay(field(), np.zeros(N), PROFILE, params(), LAPS, [], [],
                                      results(retired=("D3",)), seed=1, runs=200)
    for lap in (0, 1, 10, 20):
        live = tracker.frames[lap]
        again = replay["frames"][lap]
        assert [c["driver"] for c in live["cars"]] == [c["driver"] for c in again["cars"]]
        assert np.allclose([c["win"] for c in live["cars"]],
                           [c["win"] for c in again["cars"]])


def test_a_lapped_car_is_not_taken_for_retired():
    feed = Feed()
    tracker = live_tracker.LiveTracker(feed, Predictor(), runs=100)
    run(tracker, feed, [8])
    # Car D3 is still running at lap 8: nobody is out.
    assert not any(c["retired"] for c in tracker.frames[-1]["cars"])
    run(tracker, feed, [14])
    out = [c["driver"] for c in tracker.frames[-1]["cars"] if c["retired"]]
    assert out == ["D3"]


def test_view_sends_only_new_frames():
    feed = Feed()
    tracker = live_tracker.LiveTracker(feed, Predictor(), runs=100)
    run(tracker, feed, [4])
    view = tracker.view(since=2)
    assert view["live"] and view["season"] == 2025 and view["total_laps"] == 30
    assert [f["lap"] for f in view["frames"]] == [3, 4]


def test_no_feed_and_no_forecast_are_reported_not_raised():
    class Down(Feed):
        async def live_status(self):
            raise IngestionUnavailable("connection refused")

    class NoGrid(Predictor):
        async def live_inputs(self, *args, **kwargs):
            raise ValueError("no grid yet")

    tracker = live_tracker.LiveTracker(Down(), Predictor())
    asyncio.run(tracker.step())
    assert "not reachable" in tracker.view()["error"]
    tracker = live_tracker.LiveTracker(Feed(), NoGrid())
    asyncio.run(tracker.step())
    assert "no grid yet" in tracker.view()["error"] and tracker.race is None


def test_the_now_endpoint(monkeypatch):
    feed = Feed()
    tracker = live_tracker.LiveTracker(feed, Predictor(), runs=100)
    run(tracker, feed, [3])
    monkeypatch.setattr(live_tracker, "_tracker", tracker)
    monkeypatch.setattr(tracker, "touch", lambda: None)
    app.dependency_overrides[dependencies.get_client] = lambda: feed
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor()
    try:
        body = TestClient(app).get("/live/now", params={"since": 1}).json()
    finally:
        app.dependency_overrides.clear()
    assert [f["lap"] for f in body["frames"]] == [2, 3]
    assert sorted(c["driver"] for c in body["frames"][0]["cars"]) == sorted(DRIVERS)


class RunFeed(Feed):
    run = "2026-11-22T05:00:00+00:00"

    async def live_status(self):
        return dict(await super().live_status(), started_at=self.run)


def test_frames_go_on_the_stream_and_survive_a_restart():
    from f1_common import streams

    memory = streams.MemoryStreams()
    streams.use_client(memory)
    try:
        feed = RunFeed()
        tracker = live_tracker.LiveTracker(feed, Predictor(), runs=100)
        run(tracker, feed, [0, 3])
        stored = memory.streams[streams.frames_stream(2025, 1)]
        assert len(stored) == 4
        # A new tracker (the service restarted) picks the race up where it was.
        again = live_tracker.LiveTracker(feed, Predictor(), runs=100)
        snapshots = feed.snapshots
        run(again, feed, [3])
        assert [f["lap"] for f in again.frames] == [0, 1, 2, 3]
        assert feed.snapshots == snapshots          # nothing recomputed
        assert again.frames[3]["cars"] == tracker.frames[3]["cars"]
        # The same race recorded again starts clean.
        feed.run = "2026-11-22T06:00:00+00:00"
        run(again, feed, [1])
        assert [f["lap"] for f in again.frames] == [0, 1]
    finally:
        streams.use_client(None)


def test_the_tracker_wakes_on_the_laps_stream():
    from f1_common import streams

    memory = streams.MemoryStreams()
    streams.use_client(memory)
    try:
        tracker = live_tracker.LiveTracker(Feed(), Predictor())

        async def go():
            tracker._laps_after = await streams.last_id(streams.LAPS)
            await streams.publish(streams.LAPS, {"lap": 7})
            await asyncio.wait_for(tracker._wait(), 1)
            return tracker._laps_after

        assert asyncio.run(go()) == "1-0"
        memory.down = True
        # Redis down: falls back to the poll sleep instead of raising.
        live_tracker.POLL_SECONDS, saved = 0.01, live_tracker.POLL_SECONDS
        try:
            asyncio.run(asyncio.wait_for(tracker._wait(), 1))
        finally:
            live_tracker.POLL_SECONDS = saved
    finally:
        streams.use_client(None)


def test_a_lap_is_forecast_once_the_field_has_completed_it():
    assert live_tracker.ready_lap(0, 71) == 0
    assert live_tracker.ready_lap(1, 71) == 0      # leader done with lap 1: not yet
    assert live_tracker.ready_lap(6, 71) == 5
    assert live_tracker.ready_lap(71, 71) == 70    # the last lap before the flag
    assert live_tracker.ready_lap(5, 0) == 4


def test_gaps_are_real_when_the_leader_has_just_crossed_the_line():
    # Leader D0 has completed lap 6; the rest are still out on it.
    class Crossing(Feed):
        async def live_snapshot(self):
            self.snapshots += 1
            rows = [r.model_dump() for r in LAPS
                    if r.lap <= 5 or (r.lap == 6 and r.driver == "D0")]
            return {"lap": 6, "total_laps": 30, "laps": rows, "pit_stops": [],
                    "race_control": [], "retired": []}

    feed = Crossing()
    feed.lap = 6
    tracker = live_tracker.LiveTracker(feed, Predictor(), runs=100)
    asyncio.run(tracker.step())
    last = tracker.frames[-1]
    assert last["lap"] == 5
    gaps = [c["gap"] for c in last["cars"] if c["position"] > 1]
    assert max(gaps) < 30          # not a lap down
