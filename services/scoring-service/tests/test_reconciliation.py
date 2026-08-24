"""Reconciliation tests.

The important cases are the ones where a bad forecast could quietly disappear
from the record: a driver who was predicted but never raced, a retirement that
still carries a finishing position, and a forecast that only covered part of the
field.
"""

import pytest

from app.models.schemas import Market
from app.services.clients import (
    DriverProbabilityRow,
    LockedPrediction,
    RaceResultRow,
)
from app.services.reconciliation import (
    build_outcome,
    score_prediction,
    top_pick_was_correct,
)


def _result(driver, position, classified=None, status="Finished"):
    return RaceResultRow(
        season=2024, round=6, driver=driver, position=position,
        classified_position=str(position) if classified is None else classified,
        status=status,
    )


def _prediction(probabilities, window="pre_quali", complete=True):
    return LockedPrediction(
        prediction_id="p1", season=2024, round=6, window=window,
        model_version="v1", data_complete=complete,
        driver_probabilities=[
            DriverProbabilityRow(driver=d, p_win=w, p_podium=pod, p_points=pts)
            for d, w, pod, pts in probabilities
        ],
    )


RESULTS = [
    _result("Winner", 1),
    _result("Second", 2),
    _result("Third", 3),
    _result("Fourth", 4),
    _result("Tenth", 10),
    _result("Eleventh", 11),
    _result("Retired", 19, classified="R", status="Retired"),
]


# ── Outcomes ─────────────────────────────────────────────────────────────────


def test_outcome_flags_match_the_finishing_position():
    outcome = build_outcome(2024, 6, RESULTS)
    by_driver = {d.driver: d for d in outcome.drivers}

    assert by_driver["Winner"].won
    assert by_driver["Winner"].podium
    assert by_driver["Winner"].points

    assert not by_driver["Third"].won
    assert by_driver["Third"].podium

    assert not by_driver["Fourth"].podium
    assert by_driver["Fourth"].points

    assert by_driver["Tenth"].points
    assert not by_driver["Eleventh"].points


def test_a_retirement_scores_false_on_every_market():
    """It carries position 19, but it did not win, podium or score."""
    outcome = build_outcome(2024, 6, RESULTS)
    retired = next(d for d in outcome.drivers if d.driver == "Retired")

    assert not retired.classified
    assert not retired.won
    assert not retired.podium
    assert not retired.points


def test_exactly_one_winner():
    outcome = build_outcome(2024, 6, RESULTS)
    assert sum(1 for d in outcome.drivers if d.won) == 1


# ── Scoring a prediction ─────────────────────────────────────────────────────


def test_a_correct_confident_forecast_scores_well():
    prediction = _prediction([
        ("Winner", 0.8, 0.95, 0.99),
        ("Second", 0.1, 0.8, 0.99),
        ("Third", 0.05, 0.6, 0.95),
        ("Fourth", 0.05, 0.3, 0.9),
    ])
    score = score_prediction(prediction, build_outcome(2024, 6, RESULTS))

    win = score.market(Market.WIN)
    assert win.skill_vs_baseline > 0
    assert win.drivers_scored == 4


def test_a_predicted_driver_who_never_raced_scores_as_a_miss():
    """The rule that stops the model deleting its worst calls.

    Backing a driver who was withdrawn is a bad forecast, and dropping them from
    the scoring would make it free.
    """
    prediction = _prediction([
        ("Ghost", 0.9, 0.95, 0.99),   # not in the results at all
        ("Winner", 0.1, 0.5, 0.9),
    ])
    score = score_prediction(prediction, build_outcome(2024, 6, RESULTS))

    assert score.predicted_not_raced == ["Ghost"]
    assert score.market(Market.WIN).drivers_scored == 2
    # 0.9 on something that did not happen must hurt.
    assert score.market(Market.WIN).brier > 0.4


def test_a_driver_who_raced_but_was_not_predicted_is_recorded():
    """Not scored — there is no probability to score — but made visible.

    A forecast covering four of seven cars is not comparable to one covering
    all of them.
    """
    prediction = _prediction([("Winner", 0.9, 0.95, 0.99)])
    score = score_prediction(prediction, build_outcome(2024, 6, RESULTS))

    assert "Second" in score.raced_not_predicted
    assert "Retired" in score.raced_not_predicted
    assert score.market(Market.WIN).drivers_scored == 1


def test_all_three_markets_are_scored():
    prediction = _prediction([("Winner", 0.5, 0.8, 0.95), ("Second", 0.5, 0.7, 0.9)])
    score = score_prediction(prediction, build_outcome(2024, 6, RESULTS))

    assert {m.market for m in score.markets} == set(Market)


def test_provenance_is_carried_onto_the_score():
    prediction = _prediction([("Winner", 1.0, 1.0, 1.0)], window="post_quali", complete=False)
    score = score_prediction(prediction, build_outcome(2024, 6, RESULTS))

    assert score.window == "post_quali"
    assert score.model_version == "v1"
    assert score.prediction_id == "p1"
    assert score.data_complete is False


def test_incomplete_data_flag_survives_into_the_score():
    """A call made on partial data must stay identifiable in the aggregate."""
    complete = score_prediction(
        _prediction([("Winner", 1.0, 1.0, 1.0)], complete=True),
        build_outcome(2024, 6, RESULTS),
    )
    degraded = score_prediction(
        _prediction([("Winner", 1.0, 1.0, 1.0)], complete=False),
        build_outcome(2024, 6, RESULTS),
    )

    assert complete.data_complete
    assert not degraded.data_complete


def test_empty_prediction_does_not_crash():
    score = score_prediction(_prediction([]), build_outcome(2024, 6, RESULTS))
    assert score.market(Market.WIN).drivers_scored == 0


# ── Top pick ─────────────────────────────────────────────────────────────────


def test_top_pick_hit():
    prediction = _prediction([("Winner", 0.6, 0.9, 0.99), ("Second", 0.4, 0.8, 0.99)])
    assert top_pick_was_correct(prediction, build_outcome(2024, 6, RESULTS))


def test_top_pick_miss():
    prediction = _prediction([("Second", 0.6, 0.9, 0.99), ("Winner", 0.4, 0.8, 0.99)])
    assert not top_pick_was_correct(prediction, build_outcome(2024, 6, RESULTS))


def test_top_pick_on_an_empty_prediction_is_a_miss():
    assert not top_pick_was_correct(_prediction([]), build_outcome(2024, 6, RESULTS))
