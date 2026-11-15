"""A sprint forecast is scored against the sprint, and only the sprint.

The sprint shares its round number with a Grand Prix, so every lookup keyed on
(season, round) is a place a sprint call could be scored against the wrong
race — and the score would look entirely ordinary.
"""

from datetime import datetime, timedelta, timezone

from app.models.schemas import Market
from app.services.clients import DriverProbabilityRow, LockedPrediction, RaceResultRow
from app.services.reconciler import Reconciler
from app.services.scoring import baseline_probability

SPRINT_START = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
RACE_START = SPRINT_START + timedelta(hours=26)
DRIVERS = ["D{}".format(i) for i in range(1, 11)]


def _results(order):
    return [
        RaceResultRow(season=2026, round=13, race_name="Test GP", driver=name,
                      position=index + 1, classified_position=str(index + 1))
        for index, name in enumerate(order)
    ]


def _prediction(pid, window, start, favourite):
    return LockedPrediction(
        prediction_id=pid, season=2026, round=13, window=window,
        locked_at=start - timedelta(hours=3), race_start_utc=start,
        published_markets=["win", "podium", "points"],
        driver_probabilities=[
            DriverProbabilityRow(driver=name, p_win=0.9 if name == favourite else 0.01,
                                 p_podium=0.3, p_points=0.8)
            for name in DRIVERS
        ],
    )


class _Ingestion:
    """The Grand Prix and the sprint finish in opposite orders."""

    def __init__(self, sprint_run=True):
        self.sprint_run = sprint_run

    async def race_results(self, season, round_number):
        return _results(list(reversed(DRIVERS)))

    async def sprint_results(self, season, round_number):
        return _results(DRIVERS) if self.sprint_run else []

    async def season_results(self, season):
        # No history, so these races are scored without a grid baseline.
        return []


class _Predictions:
    def __init__(self, predictions):
        self._p = predictions

    async def predictions_for(self, season, round_number, event="race"):
        return [p for p in self._p if event == "all" or p.event == event]

    async def list_predictions(self, season=None, event="race"):
        return [p for p in self._p if event == "all" or p.event == event]


class _Store:
    def __init__(self):
        self.outcomes, self.scores = {}, []

    async def save_outcome(self, outcome):
        self.outcomes[outcome.key] = outcome

    async def save_score(self, score):
        self.scores.append(score)

    async def list_scores(self, season=None):
        return list(self.scores)

    async def list_outcomes(self, season=None):
        return list(self.outcomes.values())


def _reconciler(store, ingestion=None):
    predictions = _Predictions([
        _prediction("sprint", "sprint", SPRINT_START, favourite="D1"),
        _prediction("race", "post_quali", RACE_START, favourite="D10"),
    ])
    return Reconciler(ingestion or _Ingestion(), predictions, store)


async def test_the_sprint_forecast_is_scored_against_the_sprint():
    store = _Store()
    outcome, scores = await _reconciler(store).reconcile_race(2026, 13, "sprint")

    assert [s.prediction_id for s in scores] == ["sprint"]
    assert outcome.key == "2026-13-sprint"
    # D1 won the sprint and D10 the Grand Prix; the sprint call backed D1.
    assert scores[0].top_pick_correct is True


async def test_the_grand_prix_sweep_never_touches_the_sprint_forecast():
    store = _Store()
    _, scores = await _reconciler(store).reconcile_race(2026, 13)

    assert [s.prediction_id for s in scores] == ["race"]
    assert scores[0].top_pick_correct is True


async def test_the_season_sweep_scores_both_races_of_the_weekend():
    store = _Store()
    scored = await _reconciler(store).reconcile_season(2026)

    assert {s.prediction_id: s.window for s in scored} == {
        "sprint": "sprint", "race": "post_quali",
    }
    assert set(store.outcomes) == {"2026-13", "2026-13-sprint"}


async def test_an_unrun_sprint_waits_and_counts_as_pending():
    store = _Store()
    reconciler = _reconciler(store, _Ingestion(sprint_run=False))
    await reconciler.reconcile_season(2026)
    record = await reconciler.track_record()

    assert [s.prediction_id for s in store.scores] == ["race"]
    assert record.predictions_pending == 1


async def test_sprint_points_count_eight_places_and_so_does_its_baseline():
    store = _Store()
    outcome, scores = await _reconciler(store).reconcile_race(2026, 13, "sprint")

    assert outcome.points_positions == 8
    assert sum(d.points for d in outcome.drivers) == 8
    points = scores[0].market(Market.POINTS)
    assert points.baseline_brier == sum(
        (baseline_probability(Market.POINTS, 10, winners=8) - (1.0 if i < 8 else 0.0)) ** 2
        for i in range(10)
    ) / 10


async def test_sprint_calibration_pairs_with_the_sprint_result():
    """Calibration re-pairs every probability with what happened. Keyed on the
    round alone, a sprint's 90% on D1 would be checked against the Grand Prix,
    which D1 finished last."""
    store = _Store()
    reconciler = _reconciler(store)
    await reconciler.reconcile_season(2026)
    record = await reconciler.track_record()

    win = next(
        c for c in record.calibration
        if c.market is Market.WIN and c.window == "sprint"
    )
    top = next(b for b in win.buckets if b.lower >= 0.9)
    assert top.count == 1 and top.observed_rate == 1.0
