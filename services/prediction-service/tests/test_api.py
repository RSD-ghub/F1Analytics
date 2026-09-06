"""API-level tests against the real app with a faked ingestion service.

Covers the two things unit tests cannot: that route registration order actually
resolves the literal paths, and that immutability holds end to end through the
HTTP layer rather than only in the storage class.
"""

from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import (
    ChampionshipForecast,
    FeatureSnapshot,
    LockWindow,
    ModelVersion,
    Prediction,
)
from app.services.ingestion_client import CompletenessStatus, GridSlot, RaceResult, Weekend
from app.services.storage import PredictionExists


# ── Fakes ────────────────────────────────────────────────────────────────────


def _season_rows(season=2026, rounds=6):
    drivers = [
        ("Max Verstappen", "Red Bull"),
        ("Lando Norris", "McLaren"),
        ("Charles Leclerc", "Ferrari"),
        ("George Russell", "Mercedes"),
    ]
    rows = []
    for round_number in range(1, rounds + 1):
        for offset, (driver, team) in enumerate(drivers):
            position = ((round_number + offset) % len(drivers)) + 1
            rows.append(
                RaceResult(
                    season=season, round=round_number, race_name="R{}".format(round_number),
                    circuit="Circuit {}".format(round_number), driver=driver, team=team,
                    position=position, points=float(26 - position * 5),
                    grid_position=position,
                )
            )
    return rows


class FakeClient:
    def __init__(self, grid=True, complete=True):
        self._grid = grid
        self._complete = complete

    async def season_results(self, season):
        return _season_rows(season) if season == 2026 else []

    async def grid(self, season, round_number):
        if not self._grid:
            return []
        return [
            GridSlot(season=season, round=round_number, driver=d, team=t, position=i + 1)
            for i, (d, t) in enumerate(
                [("Max Verstappen", "Red Bull"), ("Lando Norris", "McLaren"),
                 ("Charles Leclerc", "Ferrari"), ("George Russell", "Mercedes")]
            )
        ]

    async def weekends(self, season):
        return [
            Weekend(season=2026, round=r, race_name="R{}".format(r))
            for r in range(1, 11)
        ]

    async def completeness(self, from_season, to_season, depth="full"):
        self.completeness_depth = depth
        return CompletenessStatus(
            is_complete=self._complete,
            open_gaps=[] if self._complete else ["2026-3"],
        )


class FakeStore:
    def __init__(self):
        self.predictions: Dict[str, Prediction] = {}
        self.snapshots: Dict[str, FeatureSnapshot] = {}
        self.versions: List[ModelVersion] = []
        self.championships: List[ChampionshipForecast] = []

    async def insert_prediction(self, prediction):
        key = "{}-{}-{}".format(prediction.season, prediction.round, prediction.window.value)
        if key in self.predictions:
            raise PredictionExists("already locked {}".format(key))
        self.predictions[key] = prediction
        return prediction

    async def list_predictions(self, season=None, round_number=None, limit=200):
        return [
            p for p in self.predictions.values()
            if (season is None or p.season == season)
            and (round_number is None or p.round == round_number)
        ][:limit]

    async def get_prediction(self, season, round_number, window):
        return self.predictions.get("{}-{}-{}".format(season, round_number, window.value))

    async def save_snapshot(self, snapshot):
        self.snapshots[snapshot.snapshot_id] = snapshot
        return snapshot.snapshot_id

    async def get_snapshot(self, snapshot_id):
        return self.snapshots.get(snapshot_id)

    async def record_model_version(self, version):
        self.versions.append(version)

    async def list_model_versions(self):
        return self.versions

    async def save_championship(self, forecast):
        self.championships.append(forecast)

    async def latest_championship(self, season):
        return self.championships[-1] if self.championships else None


@pytest.fixture
def client_and_store():
    from app.main import app
    from app import dependencies

    store = FakeStore()
    fake_client = FakeClient()

    app.dependency_overrides[dependencies.get_store] = lambda: store
    app.dependency_overrides[dependencies.get_client] = lambda: fake_client

    # The predictor is constructed from the other two, so it needs its own
    # override rather than picking up the ones above.
    from app.services.predictor import Predictor

    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor(
        client=fake_client, store=store, model=dependencies.get_model()
    )

    with TestClient(app) as test_client:
        yield test_client, store

    app.dependency_overrides.clear()


# ── Route resolution ─────────────────────────────────────────────────────────


def test_literal_routes_resolve_before_the_parameterised_one(client_and_store):
    """Regression guard.

    With /{season}/{round_number} registered first, these return 422 because
    "model" and "snapshot" get parsed as a season.
    """
    client, _ = client_and_store

    assert client.get("/predictions/model/versions").status_code == 200
    assert client.get("/predictions/snapshot/does-not-exist").status_code == 404


def test_race_route_still_works(client_and_store):
    client, _ = client_and_store
    assert client.get("/predictions/2026/5").status_code == 200


# ── Immutability ─────────────────────────────────────────────────────────────


def test_locking_twice_is_rejected(client_and_store):
    """The one-way door, verified through HTTP rather than only in storage."""
    client, _ = client_and_store
    body = {"season": 2026, "round": 5, "window": "pre_quali"}

    assert client.post("/predictions/lock", json=body).status_code == 201
    second = client.post("/predictions/lock", json=body)

    assert second.status_code == 409
    assert "already locked" in second.json()["detail"]


def test_both_windows_can_be_locked_for_the_same_race(client_and_store):
    """They are different predictions and are scored separately."""
    client, store = client_and_store

    assert client.post(
        "/predictions/lock", json={"season": 2026, "round": 5, "window": "pre_quali"}
    ).status_code == 201
    assert client.post(
        "/predictions/lock", json={"season": 2026, "round": 5, "window": "post_quali"}
    ).status_code == 201
    assert len(store.predictions) == 2


def test_preview_does_not_persist(client_and_store):
    client, store = client_and_store
    response = client.post(
        "/predictions/preview", json={"season": 2026, "round": 5, "window": "pre_quali"}
    )

    assert response.status_code == 200
    assert store.predictions == {}
    assert "snapshot" in response.json()


# ── Content ──────────────────────────────────────────────────────────────────


def test_locked_prediction_carries_full_provenance(client_and_store):
    client, _ = client_and_store
    body = {"season": 2026, "round": 5, "window": "pre_quali", "circuit": "Circuit 5"}
    payload = client.post("/predictions/lock", json=body).json()

    assert payload["model_version"]
    assert payload["seed"]
    assert payload["feature_snapshot_ref"]
    assert payload["locked_at"]
    assert len(payload["driver_probabilities"]) == 4


def test_pre_quali_publishes_no_win_probability(client_and_store):
    """Dropped deliberately: its win-market skill was ~0 with huge variance.

    ``None`` rather than 0.0 — the API says "no claim", not "impossible".
    """
    client, _ = client_and_store
    payload = client.post(
        "/predictions/preview", json={"season": 2026, "round": 5, "window": "pre_quali"}
    ).json()["prediction"]

    assert payload["published_markets"] == ["podium", "points"]
    assert all(d["p_win"] is None for d in payload["driver_probabilities"])
    # The markets it does claim are still there and still real.
    assert all(0.0 <= d["p_podium"] <= 1.0 for d in payload["driver_probabilities"])


def test_post_quali_win_probabilities_sum_to_one_over_http(client_and_store):
    client, _ = client_and_store
    payload = client.post(
        "/predictions/preview", json={"season": 2026, "round": 5, "window": "post_quali"}
    ).json()["prediction"]

    assert payload["published_markets"] == ["win", "podium", "points"]
    total = sum(d["p_win"] for d in payload["driver_probabilities"])
    assert total == pytest.approx(1.0, abs=1e-9)


def test_snapshot_is_retrievable_after_locking(client_and_store):
    """The reproducibility handle must actually resolve."""
    client, _ = client_and_store
    locked = client.post(
        "/predictions/lock", json={"season": 2026, "round": 5, "window": "pre_quali"}
    ).json()

    snapshot = client.get("/predictions/snapshot/{}".format(locked["feature_snapshot_ref"]))
    assert snapshot.status_code == 200
    assert snapshot.json()["as_of_round"] < 5


def test_pre_quali_snapshot_has_no_grid(client_and_store):
    client, _ = client_and_store
    payload = client.post(
        "/predictions/preview", json={"season": 2026, "round": 5, "window": "pre_quali"}
    ).json()

    assert all(d["grid_position"] == 0 for d in payload["snapshot"]["drivers"])
    assert payload["prediction"]["data_quality"]["has_grid"] is False


def test_post_quali_snapshot_uses_the_grid(client_and_store):
    client, _ = client_and_store
    payload = client.post(
        "/predictions/preview", json={"season": 2026, "round": 5, "window": "post_quali"}
    ).json()

    assert any(d["grid_position"] > 0 for d in payload["snapshot"]["drivers"])
    assert payload["prediction"]["data_quality"]["has_grid"] is True


# ── Championship ─────────────────────────────────────────────────────────────


def test_championship_returns_a_distribution(client_and_store):
    client, _ = client_and_store
    payload = client.get("/championship/2026?runs=1000").json()

    assert sum(d["p_champion"] for d in payload["drivers"]) == pytest.approx(1.0, abs=1e-9)
    assert payload["as_of_round"] == 6
    assert payload["remaining_rounds"] == [7, 8, 9, 10]


async def test_completeness_is_checked_at_results_depth(client_and_store):
    """prediction-service reads classifications and qualifying, not laps.

    Asking whether the archive is complete at full (lap-level) depth marked
    every forecast as built on incomplete data whenever laps were not
    backfilled — visible in the UI as "12 prior rounds missing" for a season
    whose twelve rounds had all ingested cleanly. A caveat that is always on is
    a caveat that stops being read.
    """
    from app import dependencies
    from app.services.predictor import Predictor
    from app.main import app

    store = FakeStore()
    recording = FakeClient()
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor(
        client=recording, store=store, model=dependencies.get_model()
    )
    client, _ = client_and_store
    client.post("/predictions/preview",
                json={"season": 2026, "round": 5, "window": "pre_quali"})

    assert recording.completeness_depth == "results"
