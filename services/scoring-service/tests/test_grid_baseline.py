"""The grid-order baseline: a forecast that knows only the starting grid."""

import pytest

from app.models.schemas import Market
from app.services.clients import DriverProbabilityRow, LockedPrediction, RaceResultRow
from app.services.grid_baseline import (
    BACK_SLOT,
    MIN_HISTORY_RACES,
    GridRates,
    HistoricalStart,
    rescale,
    slot_for,
)
from app.services.reconciliation import build_outcome, score_prediction
from app.services.track_record import build_window_record

FIELD = 20


def _history(races, season=2020, winner_slot=1):
    """``races`` past Grands Prix in which the car from ``winner_slot`` won and
    everyone else finished in grid order behind it."""
    starts = []
    for rnd in range(1, races + 1):
        order = [winner_slot] + [s for s in range(1, FIELD + 1) if s != winner_slot]
        for position, slot in enumerate(order, start=1):
            starts.append(HistoricalStart(
                season=season, round=rnd, grid_position=slot,
                position=position, classified=True,
            ))
    return starts


def test_pit_lane_and_deep_grid_share_the_back_slot():
    assert slot_for(0) == BACK_SLOT
    assert slot_for(24) == BACK_SLOT
    assert slot_for(7) == 7


def test_only_races_before_the_scored_one_count():
    """The baseline must never learn from the race it is compared with."""
    rates = GridRates.from_history(_history(30), before=(2020, 11))
    assert rates.races == 10


def test_too_little_history_gives_no_baseline():
    rates = GridRates.from_history(_history(MIN_HISTORY_RACES - 1), before=(2021, 1))
    assert not rates.usable
    assert rates.probabilities(Market.WIN, list(range(1, FIELD + 1))) is None


@pytest.mark.parametrize("market,count", [
    (Market.WIN, 1), (Market.PODIUM, 3), (Market.POINTS, 10),
])
def test_probabilities_sum_to_the_market_size_and_stay_below_one(market, count):
    rates = GridRates.from_history(_history(40), before=(2021, 1))
    probabilities = rates.probabilities(market, list(range(1, FIELD + 1)))
    assert sum(probabilities) == pytest.approx(count)
    assert all(0.0 <= p <= 1.0 for p in probabilities)


def test_pole_is_favoured_when_pole_has_always_won():
    rates = GridRates.from_history(_history(40), before=(2021, 1))
    probabilities = rates.probabilities(Market.WIN, list(range(1, FIELD + 1)))
    assert probabilities[0] > 0.8
    assert probabilities[0] > probabilities[-1]


def test_rescale_caps_at_one_and_shares_the_rest():
    result = rescale([0.9, 0.9, 0.1, 0.1], total=2.5)
    assert max(result) == pytest.approx(1.0)
    assert sum(result) == pytest.approx(2.5)


# ── Wired into scoring ───────────────────────────────────────────────────────


def _results():
    """Grid order held to the flag: P1 started first, P20 started last."""
    return [
        RaceResultRow(season=2021, round=1, driver="D{}".format(slot),
                      position=slot, classified_position=str(slot),
                      grid_position=slot)
        for slot in range(1, FIELD + 1)
    ]


def _prediction(window, favourite_slot):
    """Puts 90% on ``favourite_slot`` to win, spreading the rest evenly."""
    rest = 0.1 / (FIELD - 1)
    return LockedPrediction(
        prediction_id="p-" + window, season=2021, round=1, window=window,
        published_markets=["win"],
        driver_probabilities=[
            DriverProbabilityRow(
                driver="D{}".format(slot),
                p_win=0.9 if slot == favourite_slot else rest,
            )
            for slot in range(1, FIELD + 1)
        ],
    )


def _rates():
    return GridRates.from_history(_history(40), before=(2021, 1))


def _win_score(window, favourite_slot):
    outcome = build_outcome(2021, 1, _results())
    score = score_prediction(_prediction(window, favourite_slot), outcome, _rates())
    return score.market(Market.WIN)


def test_a_post_quali_forecast_gets_a_grid_skill():
    win = _win_score("post_quali", favourite_slot=1)
    assert win.grid_baseline_brier is not None
    assert win.skill_vs_grid is not None


def test_backing_the_wrong_car_scores_below_the_grid():
    """Backing P10 when pole wins: worse than the grid, which backed pole."""
    assert _win_score("final_grid", favourite_slot=10).skill_vs_grid < 0


def test_pre_quali_is_not_compared_with_a_forecast_that_saw_the_grid():
    win = _win_score("pre_quali", favourite_slot=1)
    assert win.grid_baseline_brier is None
    assert win.skill_vs_grid is None


def test_without_history_the_race_is_still_scored():
    outcome = build_outcome(2021, 1, _results())
    score = score_prediction(_prediction("post_quali", 1), outcome, grid_rates=None)
    win = score.market(Market.WIN)
    assert win.drivers_scored == FIELD
    assert win.skill_vs_grid is None


def test_results_without_grid_slots_get_no_grid_baseline():
    rows = [r.model_copy(update={"grid_position": 0}) for r in _results()]
    outcome = build_outcome(2021, 1, rows)
    score = score_prediction(_prediction("post_quali", 1), outcome, _rates())
    assert score.market(Market.WIN).skill_vs_grid is None


def test_the_track_record_averages_grid_skill_per_window():
    outcome = build_outcome(2021, 1, _results())
    scores = [
        score_prediction(_prediction("post_quali", slot), outcome, _rates())
        for slot in (1, 10)
    ]
    record = build_window_record("post_quali", scores)
    expected = sum(s.market(Market.WIN).skill_vs_grid for s in scores) / 2
    assert record.skill_vs_grid_by_market["win"] == pytest.approx(expected)


# ── Through the reconciler ───────────────────────────────────────────────────


class _Ingestion:
    """Past seasons where pole always won; the scored race in grid order."""

    def __init__(self, fail_history=False):
        self.fail_history = fail_history
        self.season_calls = []

    async def race_results(self, season, round_number):
        return _results()

    async def season_results(self, season):
        from app.services.clients import UpstreamUnavailable

        self.season_calls.append(season)
        if self.fail_history:
            raise UpstreamUnavailable("ingestion down")
        if season >= 2021:
            return []
        return [
            RaceResultRow(season=s.season, round=s.round, driver="x",
                          position=s.position, classified_position=str(s.position),
                          grid_position=s.grid_position)
            for s in _history(25, season=season)
        ]


class _Predictions:
    async def predictions_for(self, season, round_number, event="race"):
        return [_prediction("post_quali", favourite_slot=1)]


class _Store:
    def __init__(self):
        self.scores = []

    async def save_outcome(self, outcome):
        pass

    async def save_score(self, score):
        self.scores.append(score)


async def test_the_reconciler_scores_against_the_grid_from_past_seasons():
    from app.services.reconciler import GRID_HISTORY_SEASONS, Reconciler

    ingestion = _Ingestion()
    reconciler = Reconciler(ingestion, _Predictions(), _Store())
    _, scores = await reconciler.reconcile_race(2021, 1)

    assert scores[0].market(Market.WIN).skill_vs_grid is not None
    assert len(ingestion.season_calls) == GRID_HISTORY_SEASONS + 1

    # Finished seasons are cached; only the current one is fetched again.
    await reconciler.reconcile_race(2021, 1)
    assert len(ingestion.season_calls) == GRID_HISTORY_SEASONS + 2


async def test_a_history_outage_still_scores_the_race():
    from app.services.reconciler import Reconciler

    reconciler = Reconciler(_Ingestion(fail_history=True), _Predictions(), _Store())
    _, scores = await reconciler.reconcile_race(2021, 1)

    assert len(scores) == 1
    assert scores[0].market(Market.WIN).skill_vs_grid is None
