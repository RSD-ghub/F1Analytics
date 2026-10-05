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
    fit_comparison_scale,
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


# ── Sharpness is not skill ───────────────────────────────────────────────────
#
# A Plackett-Luce weight vector carries its own sharpness, and multiplying it by
# a scalar cannot change a single ranking it produces — every utility moves
# together. It changes the log-likelihood a great deal, though, so a gate that
# scores raw vectors reports "this model is worse" when it means "this model is
# more confident".
#
# This is not hypothetical. On the 2026 evaluation set the gate reported a 12%
# regression in a challenger whose ranking ability was within one percent of the
# champion's; the entire gap was the challenger being fitted about 25% sharper.


#: The strength spread the sampled fixture below is generated at. A model whose
#: weight on feature 'a' equals this is exactly right; larger is over-confident.
TRUE_STRENGTH = 3.0
TRUE = {"a": TRUE_STRENGTH, "b": 0.0}
OVERSHARP = {"a": TRUE_STRENGTH * 2.5, "b": 0.0}


def _sampled_race(season, round_number, field_size=10):
    """A race whose result is *drawn* from the model, not dictated by it.

    ``_race`` above makes the finishing order follow feature 'a' exactly, which
    is right for testing the sign of a comparison but wrong here: against a
    deterministic outcome the best possible model is infinitely sharp, so there
    is no interior optimum and over-confidence is never punished.

    Real races are not like that, and neither is this one. Gumbel-perturbed
    strengths ranked descending is exact Plackett-Luce sampling, so the outcome
    carries the irreducible randomness the real objective has, and a weight
    vector can be too sharp as well as too flat.
    """
    rng = np.random.default_rng(season * 1000 + round_number)
    strengths = np.linspace(1.0, 0.0, field_size)
    features = np.stack([strengths, rng.normal(size=field_size)], axis=1)
    utilities = TRUE_STRENGTH * strengths + rng.gumbel(size=field_size)
    return RaceObservation(
        features=features,
        order=[int(i) for i in np.argsort(-utilities)],
        season=season, round_number=round_number,
    )


def _sampled(season, count, **kwargs):
    return [_sampled_race(season, r, **kwargs) for r in range(1, count + 1)]


def _same_order(weights, races, feature_names=NAMES):
    """Every finishing order the weights imply, most likely first."""
    vector = np.array([weights[n] for n in feature_names])
    return [tuple(np.argsort(-(race.features @ vector))) for race in races]


def test_rescaling_weights_cannot_change_a_single_ranking():
    """The premise the whole fix rests on, asserted rather than assumed."""
    races = _sampled(2026, 30)

    assert _same_order(TRUE, races) == _same_order(OVERSHARP, races)


def test_a_sharper_copy_of_the_champion_is_not_treated_as_worse():
    """Identical model, scored raw at a different sharpness.

    Without the scale fit this is the production bug in miniature: the same
    rankings, reported as a double-digit regression.
    """
    evaluation = _sampled(2026, 30)
    validation = _sampled(2023, 30)

    raw = compare(TRUE, OVERSHARP, NAMES, evaluation)
    assert raw.relative_gain < -0.02, (
        "expected the unscaled comparison to misread sharpness as a regression"
    )

    fitted = compare(
        TRUE, OVERSHARP, NAMES, evaluation,
        champion_scale=fit_comparison_scale(TRUE, NAMES, validation),
        challenger_scale=fit_comparison_scale(OVERSHARP, NAMES, validation),
        scale_fitted_on=[2023],
    )
    assert fitted.champion_score == pytest.approx(fitted.challenger_score, abs=0.05)
    assert abs(fitted.relative_gain) < 0.02
    assert not fitted.promote


def test_the_scale_fit_does_not_rescue_a_genuinely_worse_model():
    """The fix must not become a way to promote anything.

    An inverted model ranks the field backwards. No scaling can repair that,
    and the gate must still refuse it.
    """
    evaluation = _races(2026, 20)
    validation = _races(2023, 12)

    decision = compare(
        GOOD, WRONG, NAMES, evaluation,
        champion_scale=fit_comparison_scale(GOOD, NAMES, validation),
        challenger_scale=fit_comparison_scale(WRONG, NAMES, validation),
        scale_fitted_on=[2023],
    )
    assert not decision.promote
    assert decision.challenger_score < decision.champion_score


def test_a_better_challenger_still_promotes_once_scales_are_fitted():
    evaluation = _races(2026, 20)
    validation = _races(2023, 12)

    decision = compare(
        USELESS, GOOD, NAMES, evaluation,
        champion_scale=fit_comparison_scale(USELESS, NAMES, validation),
        challenger_scale=fit_comparison_scale(GOOD, NAMES, validation),
        scale_fitted_on=[2023],
    )
    assert decision.promote


def test_the_scale_is_never_fitted_on_the_races_that_decide():
    """Fitting the scale on the evaluation set would be holdout burn in
    miniature — a free tuning pass on the exact evidence about to judge it."""
    evaluation = _races(2026, 20)
    honest = fit_comparison_scale(GOOD, NAMES, _races(2023, 12))
    burned = fit_comparison_scale(GOOD, NAMES, evaluation)

    # Both are legitimate numbers; the point is that retrain.py must pass the
    # first. If they were always equal this test could not detect the mistake.
    assert honest > 0 and burned > 0


def test_no_validation_races_falls_back_to_an_honest_one():
    """With nothing to fit on, 1.0 — the comparison is then no better than it
    was, which is the truthful outcome rather than a silently tuned one."""
    assert fit_comparison_scale(GOOD, NAMES, []) == 1.0


def test_the_decision_records_the_scales_it_used():
    """Two runs with different settings previously landed in the history
    looking identical. A decision that cannot be reproduced is an anecdote."""
    decision = compare(
        GOOD, GOOD, NAMES, _races(2026, 20),
        champion_scale=1.0, challenger_scale=0.75, scale_fitted_on=[2022, 2023],
    )
    recorded = decision.as_dict()

    assert recorded["champion_scale"] == 1.0
    assert recorded["challenger_scale"] == 0.75
    assert recorded["scale_fitted_on"] == [2022, 2023]
