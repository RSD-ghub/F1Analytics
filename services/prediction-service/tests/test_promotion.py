"""Promotion gate tests.

The gate is what makes continuous retraining an improvement loop rather than a
random walk. Three failure modes are tested, because each one silently degrades
the model while looking like progress:

* promoting on noise — swapping models on a few lucky races,
* promoting a worse model — the sign of the comparison being wrong,
* holdout burn — re-testing on the same races every cycle until something
  eventually beats them by luck.

The last is the subtle one, and the only one that gets worse the longer the
system runs.
"""

import numpy as np
import pytest

from app.training.plackett_luce import RaceObservation
from app.training.promotion import (
    MIN_EVALUATION_RACES,
    compare,
    unseen_races,
)

NAMES = ["a", "b"]


def _race(season, round_number, signal=1.0, field_size=10):
    """A race where feature 'a' genuinely predicts the finishing order."""
    rng = np.random.default_rng(season * 100 + round_number)
    strengths = np.linspace(1.0, 0.0, field_size) * signal
    features = np.stack([strengths, rng.normal(size=field_size)], axis=1)
    # Finishing order follows feature 'a' exactly, so a positive weight on it
    # is the correct model and a zero weight is the uninformed one.
    return RaceObservation(
        features=features, order=list(range(field_size)),
        season=season, round_number=round_number,
    )


def _races(season, count, **kwargs):
    return [_race(season, r, **kwargs) for r in range(1, count + 1)]


GOOD = {"a": 3.0, "b": 0.0}      # knows the signal
USELESS = {"a": 0.0, "b": 0.0}   # uniform
WRONG = {"a": -3.0, "b": 0.0}    # inverted


# ── The comparison ───────────────────────────────────────────────────────────


def test_a_genuinely_better_challenger_is_promoted():
    decision = compare(USELESS, GOOD, NAMES, _races(2026, 20))

    assert decision.promote
    assert decision.challenger_score > decision.champion_score
    assert decision.relative_gain > 0


def test_a_worse_challenger_is_rejected():
    decision = compare(GOOD, WRONG, NAMES, _races(2026, 20))

    assert not decision.promote
    assert decision.challenger_score < decision.champion_score


def test_an_identical_challenger_is_rejected():
    """No gain means no swap. Churning versions fragments the track record."""
    decision = compare(GOOD, GOOD, NAMES, _races(2026, 20))

    assert not decision.promote
    assert decision.relative_gain == pytest.approx(0.0, abs=1e-9)


def test_a_marginal_gain_is_rejected_as_noise():
    """Twenty races cannot distinguish a 0.1% improvement from luck."""
    barely = {"a": 3.001, "b": 0.0}
    decision = compare(GOOD, barely, NAMES, _races(2026, 20))

    assert not decision.promote
    assert "below the" in decision.reason


def test_a_useless_incumbent_is_replaced_by_anything_better():
    """The divide-by-zero guard: no edge over uniform means any gain counts."""
    decision = compare(USELESS, GOOD, NAMES, _races(2026, 20))

    assert decision.promote
    assert decision.baseline_score == pytest.approx(decision.champion_score, abs=1e-9)


# ── Sample size ──────────────────────────────────────────────────────────────


def test_too_few_races_holds_regardless_of_apparent_gain():
    """A huge apparent improvement on three races is not evidence."""
    decision = compare(USELESS, GOOD, NAMES, _races(2026, 3))

    assert not decision.promote
    assert "need {}".format(MIN_EVALUATION_RACES) in decision.reason


def test_no_races_at_all_holds():
    decision = compare(USELESS, GOOD, NAMES, [])
    assert not decision.promote
    assert decision.evaluation_races == 0


# ── Holdout burn ─────────────────────────────────────────────────────────────


def test_evaluation_excludes_everything_the_champion_trained_on():
    observations = _races(2024, 5) + _races(2025, 5) + _races(2026, 5)
    fresh = unseen_races(observations, champion_trained_through=2025,
                         champion_evaluated_through=None)

    assert {o.season for o in fresh} == {2026}


def test_evaluation_excludes_races_that_decided_the_last_promotion():
    """The anti-holdout-burn rule, and the reason the loop stays honest.

    Re-testing on the same races every cycle and shipping whatever wins is a
    slow way to overfit the holdout: each cycle spends a little of its validity
    until the "held-out" score is a training score in disguise.
    """
    observations = _races(2024, 5) + _races(2025, 5) + _races(2026, 5)
    fresh = unseen_races(observations, champion_trained_through=2023,
                         champion_evaluated_through=2025)

    assert {o.season for o in fresh} == {2026}
    assert all(o.season > 2025 for o in fresh)


def test_a_champion_evaluated_on_everything_has_nothing_left_to_test_on():
    """Correctly yields no decision rather than reusing stale evidence."""
    observations = _races(2024, 5) + _races(2025, 5)
    fresh = unseen_races(observations, champion_trained_through=2023,
                         champion_evaluated_through=2025)

    assert fresh == []


def test_the_later_of_the_two_floors_wins():
    observations = _races(2024, 5) + _races(2025, 5) + _races(2026, 5)

    assert {o.season for o in unseen_races(observations, 2025, 2024)} == {2026}
    assert {o.season for o in unseen_races(observations, 2024, 2025)} == {2026}


# ── Provenance ───────────────────────────────────────────────────────────────


def test_the_decision_records_its_evidence():
    decision = compare(USELESS, GOOD, NAMES, _races(2026, 20))
    payload = decision.as_dict()

    assert payload["evaluation_races"] == 20
    assert payload["evaluated_seasons"] == [2026]
    assert payload["decided_at"]
    assert payload["reason"]


def test_a_rejection_is_recorded_as_fully_as_a_promotion():
    """A run of rejections means the features have stopped improving.

    That is worth knowing, and invisible if only successes are written down.
    """
    decision = compare(GOOD, WRONG, NAMES, _races(2026, 20))
    payload = decision.as_dict()

    assert payload["promote"] is False
    assert payload["champion_score"] != 0.0
    assert payload["challenger_score"] != 0.0
    assert payload["reason"]
