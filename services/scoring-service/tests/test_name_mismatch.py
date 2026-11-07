"""A forecast whose driver names disagree with the results is held, not scored.

Austin 2026: the forecast said "Kimi Antonelli", the results said "Andrea Kimi
Antonelli", and the race was scored as a confident miss on every market even
though the call was right. Scoring joins on the name, so a spelling split must
stop the score rather than quietly produce a wrong one.
"""

import pytest

from app.services.clients import DriverProbabilityRow, LockedPrediction, RaceResultRow
from app.services.reconciler import Reconciler
from app.services.reconciliation import likely_same_driver, name_mismatches


@pytest.mark.parametrize("a,b", [
    ("Kimi Antonelli", "Andrea Kimi Antonelli"),
    ("Sergio Pérez", "Sergio Perez"),
    ("Nico Hülkenberg", "Nico Hulkenberg"),
    ("Zhou Guanyu", "Guanyu Zhou"),
])
def test_known_spelling_splits_are_one_driver(a, b):
    assert likely_same_driver(a, b)


@pytest.mark.parametrize("a,b", [
    ("Mick Schumacher", "Ralf Schumacher"),
    ("Max Verstappen", "Jos Verstappen"),
    ("Lando Norris", "Oscar Piastri"),
    ("Antonelli", "Andrea Kimi Antonelli"),
])
def test_different_drivers_are_not_merged(a, b):
    """One shared word is not enough: a substitute can share a surname."""
    assert not likely_same_driver(a, b)


def test_a_genuine_substitution_is_not_a_mismatch():
    assert name_mismatches(["Lance Stroll"], ["Felipe Drugovich"]) == []


# ── Through the reconciler ───────────────────────────────────────────────────

FORECAST = ["Kimi Antonelli", "George Russell", "Lando Norris"]
RESULTS = ["Andrea Kimi Antonelli", "George Russell", "Lando Norris"]


def _results(names):
    return [
        RaceResultRow(season=2026, round=18, driver=name, position=index + 1,
                      classified_position=str(index + 1))
        for index, name in enumerate(names)
    ]


class _Ingestion:
    def __init__(self, names):
        self.names = names

    async def race_results(self, season, round_number):
        return _results(self.names)

    async def season_results(self, season):
        return []


class _Predictions:
    def __init__(self):
        self.predictions = [LockedPrediction(
            prediction_id="austin", season=2026, round=18, window="final_grid",
            published_markets=["win", "podium", "points"],
            driver_probabilities=[
                DriverProbabilityRow(driver=name, p_win=p, p_podium=0.9, p_points=0.99)
                for name, p in zip(FORECAST, (0.6, 0.3, 0.1))
            ],
        )]

    async def predictions_for(self, season, round_number, event="race"):
        return self.predictions

    async def list_predictions(self, season=None, event="race"):
        return self.predictions


class _Store:
    def __init__(self):
        self.outcomes, self.scores = {}, {}

    async def save_outcome(self, outcome):
        self.outcomes[outcome.key] = outcome.model_copy(deep=True)

    async def save_score(self, score):
        self.scores[score.prediction_id] = score

    async def list_scores(self, season=None):
        return list(self.scores.values())

    async def list_outcomes(self, season=None):
        return list(self.outcomes.values())


async def test_a_split_name_holds_the_forecast_instead_of_scoring_a_miss():
    store = _Store()
    reconciler = Reconciler(_Ingestion(RESULTS), _Predictions(), store)
    outcome, scores = await reconciler.reconcile_race(2026, 18)

    assert scores == []
    assert store.scores == {}
    [held] = outcome.held
    assert held.prediction_id == "austin"
    assert [(m.forecast_name, m.result_name) for m in held.mismatches] == [
        ("Kimi Antonelli", "Andrea Kimi Antonelli")
    ]
    # Persisted with the outcome, so the track record can report it.
    assert store.outcomes["2026-18"].held


async def test_the_track_record_reports_held_forecasts_apart_from_pending():
    store = _Store()
    reconciler = Reconciler(_Ingestion(RESULTS), _Predictions(), store)
    await reconciler.reconcile_race(2026, 18)
    record = await reconciler.track_record(2026)

    assert record.predictions_held_name_mismatch == 1
    assert record.predictions_pending == 0


async def test_once_the_names_agree_the_forecast_is_scored_and_released():
    store = _Store()
    predictions = _Predictions()
    await Reconciler(_Ingestion(RESULTS), predictions, store).reconcile_race(2026, 18)

    # Names fixed at ingestion; the next reconcile scores it.
    fixed = Reconciler(_Ingestion(FORECAST), predictions, store)
    outcome, scores = await fixed.reconcile_race(2026, 18)

    assert [s.prediction_id for s in scores] == ["austin"]
    assert outcome.held == []
    assert scores[0].top_pick_correct is True
    record = await fixed.track_record(2026)
    assert record.predictions_held_name_mismatch == 0


def test_the_reconcile_route_says_why_it_held():
    from fastapi.testclient import TestClient

    from app.dependencies import get_reconciler
    from app.main import app

    store = _Store()
    app.dependency_overrides[get_reconciler] = lambda: Reconciler(
        _Ingestion(RESULTS), _Predictions(), store
    )
    try:
        # No context manager: the app's startup would launch the scheduler.
        body = TestClient(app).post("/reconcile/2026/18").json()
    finally:
        app.dependency_overrides.clear()

    assert body["scored"] == 0
    assert body["held"][0]["prediction_id"] == "austin"
    assert "Kimi Antonelli / Andrea Kimi Antonelli" in body["message"]
