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
    async def go():
        for lap in laps:
            feed.lap = lap
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
