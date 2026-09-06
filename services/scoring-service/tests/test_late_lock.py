"""Refusing to score a forecast that was not a forecast.

The product's entire claim is "we said it beforehand". A prediction locked at or
after the race started fails that claim, and scoring it would place a
result-shaped entry into the public accuracy record — the one number the whole
thing is selling. So it is refused outright rather than flagged, because a
flagged score still lands in the aggregate.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.clients import LockedPrediction

RACE_START = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)


def _prediction(locked_at, race_start=RACE_START, pid="p1"):
    return LockedPrediction(
        prediction_id=pid, season=2026, round=13, window="post_quali",
        locked_at=locked_at, race_start_utc=race_start,
        published_markets=["win", "podium", "points"],
        driver_probabilities=[],
    )


# ── The three states ─────────────────────────────────────────────────────────


def test_a_forecast_locked_before_the_race_is_not_late():
    assert _prediction(RACE_START - timedelta(hours=5)).is_late_lock is False


def test_a_lock_after_the_start_is_late():
    assert _prediction(RACE_START + timedelta(minutes=1)).is_late_lock is True


def test_a_lock_exactly_at_the_start_is_late():
    """The lights going out is the deadline, not a grace point."""
    assert _prediction(RACE_START).is_late_lock is True


def test_missing_timestamps_are_unknown_not_fine():
    """Three-valued on purpose.

    Returning False here would let any prediction without a recorded race start
    pass the guard silently — turning a gap in the evidence into a clean bill of
    health, which is the failure this whole guard exists to prevent.
    """
    assert _prediction(RACE_START - timedelta(hours=5), race_start=None).is_late_lock is None
    assert _prediction(None).is_late_lock is None


def test_naive_and_aware_timestamps_do_not_raise():
    """Mongo round-trips can drop tzinfo; the guard must still decide."""
    naive = datetime(2026, 9, 6, 7, 27)
    assert _prediction(naive).is_late_lock is False


# ── The guard in the reconciler ──────────────────────────────────────────────


class _Predictions:
    def __init__(self, predictions):
        self._p = predictions

    async def predictions_for(self, season, round_number):
        return self._p

    async def list_predictions(self, season=None):
        return self._p


class _Store:
    def __init__(self):
        self.saved = []

    async def save_outcome(self, outcome):
        pass

    async def save_score(self, score):
        self.saved.append(score)

    async def list_scores(self, season=None):
        return list(self.saved)


class _Ingestion:
    def __init__(self, results):
        self._r = results

    async def race_results(self, season, round_number):
        return self._r


def _results():
    from app.services.clients import RaceResultRow
    return [
        RaceResultRow(season=2026, round=13, race_name="Italian Grand Prix",
                      driver="D{}".format(i), team="T", position=i, points=0.0)
        for i in range(1, 11)
    ]


async def test_a_late_lock_is_refused_and_never_scored():
    from app.services.reconciler import Reconciler

    store = _Store()
    late = _prediction(RACE_START + timedelta(hours=1), pid="late")
    reconciler = Reconciler(
        ingestion=_Ingestion(_results()),
        predictions=_Predictions([late]),
        store=store,
    )
    _outcome, scores = await reconciler.reconcile_race(2026, 13)

    assert scores == []
    assert store.saved == []


async def test_an_on_time_lock_is_still_scored():
    from app.services.reconciler import Reconciler

    store = _Store()
    good = _prediction(RACE_START - timedelta(hours=5), pid="good")
    reconciler = Reconciler(
        ingestion=_Ingestion(_results()),
        predictions=_Predictions([good]),
        store=store,
    )
    _outcome, scores = await reconciler.reconcile_race(2026, 13)

    assert len(scores) == 1
    assert scores[0].prediction_id == "good"


async def test_a_refused_prediction_is_not_reported_as_merely_pending():
    """Otherwise it reads as a scoring backlog rather than a rejected forecast —
    the opposite claim about the record's integrity."""
    from app.services.reconciler import Reconciler

    late = _prediction(RACE_START + timedelta(hours=1), pid="late")
    reconciler = Reconciler(
        ingestion=_Ingestion(_results()),
        predictions=_Predictions([late]),
        store=_Store(),
    )
    record = await reconciler.track_record()

    assert record.predictions_refused_late == 1
    assert record.predictions_pending == 0
    assert record.predictions_scored == 0
