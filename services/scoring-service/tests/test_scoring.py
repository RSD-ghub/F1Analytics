"""Scoring rule tests, including the properties that make the rules *proper*.

Hand-checkable values are used throughout. A scoring bug does not crash — it
produces a plausible number that makes the model look better or worse than it is,
and the accuracy record is the one thing in this system that must not be quietly
wrong. So the arithmetic is pinned against values computed by hand.
"""

import math

import pytest

from app.models.schemas import Market
from app.services.scoring import (
    LOG_EPSILON,
    baseline_probability,
    brier_score,
    build_calibration,
    log_score,
    score_market,
    skill_score,
)


# ── Brier ────────────────────────────────────────────────────────────────────


def test_perfect_forecast_scores_zero():
    assert brier_score([1.0, 0.0, 0.0], [True, False, False]) == 0.0


def test_maximally_wrong_forecast_scores_one():
    assert brier_score([0.0, 1.0], [True, False]) == 1.0


def test_hand_checked_value():
    """0.7 on a hit, 0.2 on a miss: (0.09 + 0.04) / 2."""
    assert brier_score([0.7, 0.2], [True, False]) == pytest.approx(0.065)


def test_total_uncertainty_scores_a_quarter():
    assert brier_score([0.5, 0.5], [True, False]) == pytest.approx(0.25)


def test_empty_input_is_not_an_error():
    assert brier_score([], []) == 0.0


def test_brier_is_proper_honesty_wins():
    """The defining property.

    If an event truly happens 30% of the time, stating 0.3 must minimise expected
    Brier score. Overstating or understating both cost more. This is why the
    product can promise honest probabilities and measure them with this rule —
    under a naive accuracy measure, claiming 99% would score better.
    """
    truth = 0.3

    def expected(stated):
        return truth * (stated - 1) ** 2 + (1 - truth) * stated**2

    honest = expected(0.3)
    assert honest < expected(0.1)
    assert honest < expected(0.5)
    assert honest < expected(0.9)


# ── Log score ────────────────────────────────────────────────────────────────


def test_log_score_of_certainty_is_zero():
    assert log_score([1.0], [True]) == pytest.approx(0.0, abs=1e-5)


def test_log_score_punishes_confident_mistakes_far_harder_than_brier():
    """Its severity is the point: it makes hedging irrational when uncertain."""
    confident_miss_brier = brier_score([0.01], [True])
    unsure_miss_brier = brier_score([0.4], [True])
    confident_miss_log = log_score([0.01], [True])
    unsure_miss_log = log_score([0.4], [True])

    assert confident_miss_brier / unsure_miss_brier < 3
    assert confident_miss_log / unsure_miss_log > 4


def test_zero_probability_does_not_produce_infinity():
    """Real forecasts emit 0.000 for backmarkers; one surprise must not
    swamp every prediction ever made."""
    score = log_score([0.0], [True])

    assert math.isfinite(score)
    assert score == pytest.approx(-math.log(LOG_EPSILON), rel=1e-6)


def test_log_score_is_proper_too():
    truth = 0.3

    def expected(stated):
        clipped = min(max(stated, LOG_EPSILON), 1 - LOG_EPSILON)
        return -(truth * math.log(clipped) + (1 - truth) * math.log(1 - clipped))

    assert expected(0.3) < expected(0.15)
    assert expected(0.3) < expected(0.6)


# ── Baseline and skill ───────────────────────────────────────────────────────


def test_baseline_reflects_how_many_drivers_can_satisfy_the_market():
    assert baseline_probability(Market.WIN, 20) == pytest.approx(0.05)
    assert baseline_probability(Market.PODIUM, 20) == pytest.approx(0.15)
    assert baseline_probability(Market.POINTS, 20) == pytest.approx(0.5)


def test_baseline_is_capped_at_certainty():
    """Ten points-payers in an eight-car field: everyone scores."""
    assert baseline_probability(Market.POINTS, 8) == 1.0


def test_skill_is_zero_when_the_model_matches_the_baseline():
    assert skill_score(0.05, 0.05) == 0.0


def test_skill_is_positive_when_the_model_beats_the_baseline():
    assert skill_score(0.025, 0.05) == pytest.approx(0.5)


def test_skill_is_negative_when_guessing_would_have_been_better():
    """The number that stops a useless model from looking respectable."""
    assert skill_score(0.10, 0.05) == pytest.approx(-1.0)


def test_perfect_model_has_skill_of_one():
    assert skill_score(0.0, 0.05) == 1.0


def test_degenerate_baseline_does_not_divide_by_zero():
    assert skill_score(0.0, 0.0) == 0.0


# ── Market scoring ───────────────────────────────────────────────────────────


def _twenty_car_win_market(top_probability):
    """One driver favoured, the rest sharing the remainder."""
    rest = (1.0 - top_probability) / 19
    probabilities = [top_probability] + [rest] * 19
    outcomes = [True] + [False] * 19
    return probabilities, outcomes


def test_a_confident_correct_call_beats_the_baseline():
    probabilities, outcomes = _twenty_car_win_market(0.8)
    score = score_market(Market.WIN, probabilities, outcomes)

    assert score.skill_vs_baseline > 0.5
    assert score.drivers_scored == 20
    assert score.baseline_brier > score.brier


def test_a_confident_wrong_call_scores_worse_than_guessing():
    probabilities, outcomes = _twenty_car_win_market(0.8)
    outcomes = [False] + [False] * 18 + [True]  # the favourite lost
    score = score_market(Market.WIN, probabilities, outcomes)

    assert score.skill_vs_baseline < 0


def test_empty_market_is_not_an_error():
    score = score_market(Market.WIN, [], [])
    assert score.drivers_scored == 0
    assert score.brier == 0.0


# ── Calibration ──────────────────────────────────────────────────────────────


def test_a_perfectly_calibrated_model_has_near_zero_error():
    """Of everything called 30% likely, 30% happened."""
    samples = []
    for _ in range(70):
        samples.append((0.3, False))
    for _ in range(30):
        samples.append((0.3, True))

    curve = build_calibration(samples, Market.WIN, buckets=10)

    assert curve.expected_calibration_error == pytest.approx(0.0, abs=1e-9)
    assert len(curve.buckets) == 1
    assert curve.buckets[0].observed_rate == pytest.approx(0.3)


def test_an_overconfident_model_is_exposed():
    """Claims 90%, delivers 50%."""
    samples = [(0.9, index % 2 == 0) for index in range(100)]
    curve = build_calibration(samples, Market.WIN, buckets=10)

    assert curve.buckets[0].gap == pytest.approx(0.4)
    assert curve.expected_calibration_error == pytest.approx(0.4)


def test_an_underconfident_model_shows_a_negative_gap():
    samples = [(0.2, True) for _ in range(50)] + [(0.2, False) for _ in range(10)]
    curve = build_calibration(samples, Market.WIN, buckets=10)

    assert curve.buckets[0].gap < 0


def test_empty_buckets_are_dropped_not_reported_as_zero():
    """A band with no predictions is absence of evidence.

    Plotting it at 0% observed would draw a curve that looks badly
    miscalibrated exactly where nothing was ever claimed.
    """
    curve = build_calibration([(0.05, False), (0.95, True)], Market.WIN, buckets=10)

    assert len(curve.buckets) == 2
    assert [b.count for b in curve.buckets] == [1, 1]


def test_certainty_lands_in_the_top_bucket_not_out_of_range():
    """p = 1.0 would index past the last bucket without clamping."""
    curve = build_calibration([(1.0, True)], Market.WIN, buckets=10)

    assert len(curve.buckets) == 1
    assert curve.buckets[0].upper == pytest.approx(1.0)


def test_calibration_error_is_weighted_by_bucket_size():
    """A miscalibrated band with two samples must not outweigh a good one
    with two hundred."""
    samples = [(0.5, index % 2 == 0) for index in range(200)]  # well calibrated
    samples += [(0.9, False), (0.9, False)]                    # badly, but rare
    curve = build_calibration(samples, Market.WIN, buckets=10)

    assert curve.expected_calibration_error < 0.02


def test_no_samples_produces_an_empty_curve():
    curve = build_calibration([], Market.WIN)
    assert curve.samples == 0
    assert curve.buckets == []
    assert curve.expected_calibration_error == 0.0
