"""The race replay behind the Live page: a finished race, forecast lap by lap."""

import json

import numpy as np
from fastapi.testclient import TestClient

from app import dependencies
from app.main import app
from app.routers import live as live_router
from app.services import live_replay
from app.services.ingestion_client import RaceControlMessage
from app.training.live_network import LiveNetwork

from tests.test_lap_simulation import PROFILE, params
from tests.test_live_forecast import DRIVERS, N, field, results, scripted_race


def build(**kwargs):
    defaults = dict(laps=scripted_race(pace=[-0.5, 0, 0, 0, 0, 0]), pit_stops=[],
                    messages=[], results=results(), seed=1, runs=200)
    defaults.update(kwargs)
    return live_replay.build_replay(field(), np.zeros(N), PROFILE, params(), **defaults)


def test_a_frame_for_every_lap_from_the_grid_to_the_last_lap():
    replay = build()
    assert replay["total_laps"] == 30
    assert [f["lap"] for f in replay["frames"]] == list(range(30))
    for frame in replay["frames"]:
        assert sorted(c["driver"] for c in frame["cars"]) == sorted(DRIVERS)
        assert abs(sum(c["win"] for c in frame["cars"]) - 1.0) < 1e-6
        assert [c["position"] for c in frame["cars"]] == list(range(1, N + 1))


def test_the_leader_grows_more_certain_as_the_race_goes_on():
    replay = build()
    leader = [next(c for c in f["cars"] if c["driver"] == "D0")["win"]
              for f in replay["frames"]]
    assert leader[-1] > leader[1]
    assert leader[-1] > 0.8


def test_a_step_keeps_the_grid_and_the_last_lap():
    assert live_replay.replay_laps(30, 7) == [0, 7, 14, 21, 28, 29]
    assert [f["lap"] for f in build(step=10)["frames"]] == [0, 10, 20, 29]


def test_the_result_and_tyres_come_through():
    replay = build()
    assert [r["driver"] for r in replay["result"]] == DRIVERS
    lap20 = next(f for f in replay["frames"] if f["lap"] == 20)
    car = lap20["cars"][0]
    assert car["compound"] == "HARD" and car["stops"] == 1
    assert lap20["cars"][0]["gap"] == 0.0


def test_retired_cars_are_marked_and_last():
    laps = scripted_race(retire=("D2", 10))
    replay = build(laps=laps, results=results(retired=("D2",)))
    frame = next(f for f in replay["frames"] if f["lap"] == 20)
    assert frame["cars"][-1]["driver"] == "D2"
    assert frame["cars"][-1]["retired"] and frame["cars"][-1]["win"] == 0.0
    last = replay["result"][-1]
    assert last["driver"] == "D2" and last["position"] is None


def test_a_safety_car_is_flagged():
    messages = [RaceControlMessage(season=2025, round=1, category="SafetyCar",
                                   message="SAFETY CAR DEPLOYED", lap=12)]
    replay = build(messages=messages)
    assert next(f for f in replay["frames"] if f["lap"] == 12)["flag"] == "SC"
    assert next(f for f in replay["frames"] if f["lap"] == 5)["flag"] == ""


def zero_network(hidden=2):
    """A network that changes nothing: the simulator's chances, renormalised."""
    from app.training.live_network import FEATURES
    net = LiveNetwork(hidden=hidden)
    k = net.inputs = len(FEATURES)
    net.mean, net.std = np.zeros(k), np.ones(k)
    net.weights = np.zeros(2 * k + hidden * k + 3 * hidden)
    return net


def test_the_network_corrects_dry_laps_and_leaves_wet_ones_alone():
    plain = build()
    dry = build(network=zero_network())
    assert dry["network"]
    assert [f["corrected"] for f in dry["frames"]] == [False] + [True] * 29
    lap20 = {c["driver"]: c["win"] for c in plain["frames"][20]["cars"]}
    for car in dry["frames"][20]["cars"]:
        # Only the floor on a zero chance (0.01) moves anything.
        assert abs(car["win"] - lap20[car["driver"]]) < 0.02

    wet_laps = [lap.model_copy(update={"compound": "INTERMEDIATE"})
                for lap in scripted_race()]
    wet = build(laps=wet_laps, network=zero_network())
    assert wet["wet"]
    assert not any(f["corrected"] for f in wet["frames"])


def test_an_artifact_that_failed_its_gate_is_not_used(tmp_path):
    path = tmp_path / "live_network.json"
    path.write_text(json.dumps({"enabled": False, "network": {}}))
    assert live_replay.load_live_network(str(path))[0] is None
    assert live_replay.load_live_network(str(tmp_path / "missing.json")) == (None, None)


def test_the_endpoint_says_404_for_a_race_it_cannot_replay():
    class Predictor:
        async def replay(self, *args, **kwargs):
            from app.services.predictor import ReplayUnavailable
            raise ReplayUnavailable("no lap timing ingested for 2025-1")

    class Client:
        async def weekends(self, season):
            return []

    live_router.clear_cache()
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor()
    app.dependency_overrides[dependencies.get_client] = lambda: Client()
    try:
        response = TestClient(app).get("/live/2025/1/replay")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert "lap timing" in response.json()["detail"]


def test_the_endpoint_caches_a_replay():
    calls = []

    class Predictor:
        async def replay(self, season, round_number, **kwargs):
            calls.append((season, round_number))
            return {"frames": [], "season": season, "round": round_number}

    class Client:
        async def weekends(self, season):
            return []

    live_router.clear_cache()
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor()
    app.dependency_overrides[dependencies.get_client] = lambda: Client()
    try:
        client = TestClient(app)
        assert client.get("/live/2025/3/replay").status_code == 200
        assert client.get("/live/2025/3/replay").json()["round"] == 3
    finally:
        app.dependency_overrides.clear()
        live_router.clear_cache()
    assert calls == [(2025, 3)]


def test_the_network_stays_off_once_anyone_has_run_a_wet_tyre():
    laps = [lap.model_copy(update={"compound": "INTERMEDIATE"})
            if lap.lap <= 5 and lap.driver == "D3" else lap for lap in scripted_race()]
    replay = build(laps=laps, network=zero_network())
    assert not any(f["corrected"] for f in replay["frames"])
