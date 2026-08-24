"""Tests for the fitted weights artifact and how it is loaded.

Separate from the mechanical model tests on purpose. These assert things about
the *trained* model — that it exists, that its signs are physically sensible,
and that a missing artifact fails loudly rather than silently serving guesses.

The sign checks are the valuable ones. A sign error in a fitted weight produces
a model that is confidently, systematically backwards while every probability
still sums to 1.0 and every calibration mechanism keeps working.
"""

import json
import os

import pytest

from app.models.schemas import DriverFeatures
from app.services.model import (
    BASE_FEATURES,
    GRID_FEATURES,
    WEIGHTS_PATH,
    ModelWeightsMissing,
    RaceModel,
)

ARTIFACT = os.path.abspath(WEIGHTS_PATH)
missing = not os.path.exists(ARTIFACT)
skip_if_untrained = pytest.mark.skipif(
    missing, reason="model_weights.json not built; run scripts/train_model.py"
)


# ── Loading ──────────────────────────────────────────────────────────────────


def test_missing_artifact_raises_rather_than_serving_guesses(tmp_path):
    """The most important behaviour here.

    Untrained weights produce output shaped exactly like trained output — same
    fields, same ranges, probabilities still summing to one. Nothing downstream
    could detect the difference, and the predictions would land in a permanent
    public record. So absence must be an error, never a fallback.
    """
    with pytest.raises(ModelWeightsMissing):
        RaceModel.load(path=str(tmp_path / "does-not-exist.json"))


@skip_if_untrained
def test_artifact_loads_and_reports_its_provenance():
    model = RaceModel.load()

    assert model.weights
    assert model.version
    with open(ARTIFACT) as handle:
        artifact = json.load(handle)
    assert artifact["training_seasons"]
    assert artifact["fitted_at"]
    assert artifact["metrics"]


@skip_if_untrained
def test_each_window_has_its_own_fitted_temperature():
    """The two windows need different confidence, and it is not 1.0.

    MLE fits weights to explain whole finishing orders, which leaves the
    top-of-order markets the product publishes systematically under-confident —
    so a temperature is fitted afterwards per window. Sharing one across both
    windows was measurably harmful: a value suited to the grid-aware model drove
    the grid-blind model's win-market skill to -43%.
    """
    model = RaceModel.load()

    assert 0.0 < model.noise_scale < 3.0
    assert 0.0 < model.pre_quali_noise_scale < 3.0
    # The grid-blind window knows less, so it must be the less confident of the
    # two — a lower temperature means sharper probabilities.
    assert model.pre_quali_noise_scale >= model.noise_scale


@skip_if_untrained
def test_every_expected_feature_has_a_weight():
    weights = RaceModel.load().weights
    for name in BASE_FEATURES:
        assert name in weights, "no fitted weight for {}".format(name)


# ── Physical plausibility ────────────────────────────────────────────────────


#: Features that all measure "how well has this driver been going", in units
#: where lower is better. They are strongly correlated with each other, so the
#: fit redistributes weight among them freely and an individual sign carries no
#: meaning — ``avg_finish_recent`` has come back positive while the model as a
#: whole was demonstrably better out-of-sample.
CORRELATED_PACE_FEATURES = (
    "avg_finish_recent",
    "avg_finish_season",
    "team_avg_finish",
    "avg_grid_recent",
)


@skip_if_untrained
def test_pace_features_point_the_right_way_in_aggregate():
    """Better recent running must raise strength — checked as a block.

    Asserting the sign of each feature individually looks stricter and is
    actually wrong: these four are collinear, so the optimiser can put a
    positive weight on one and a larger negative weight on another and describe
    exactly the same model. That produced a red test on a model that was better
    on every held-out market.

    The sum is the claim that survives collinearity: taken together, worse
    finishing positions must not raise a driver's strength.
    """
    weights = RaceModel.load().weights
    total = sum(weights.get(name, 0.0) for name in CORRELATED_PACE_FEATURES)

    assert total < 0, "pace features collectively reward finishing badly: {}".format(
        {name: round(weights.get(name, 0.0), 4) for name in CORRELATED_PACE_FEATURES}
    )


@skip_if_untrained
@pytest.mark.parametrize("feature", ["grid_modern", "quali_gap_pct"])
def test_isolated_features_keep_their_physical_sign(feature):
    """These two are not collinear with the pace block, so their signs do mean
    something: a better grid slot and a smaller gap to pole must both help."""
    assert RaceModel.load().weights[feature] < 0


@skip_if_untrained
def test_grid_weight_is_negative_in_the_modern_era():
    """Starting further forward must help."""
    assert RaceModel.load().weights["grid_modern"] < 0


@skip_if_untrained
def test_scoring_rate_has_a_positive_weight():
    assert RaceModel.load().weights["points_per_race"] > 0


# ── Behaviour of the trained model ───────────────────────────────────────────


def _driver(name, form, grid=0, dnf=0.0, team="Team"):
    return DriverFeatures(
        driver=name, team=team, avg_finish_recent=form, avg_finish_season=form,
        team_avg_finish=form, circuit_avg_finish=form, points_per_race=max(0.0, 26 - 2 * form),
        dnf_rate=dnf, grid_position=grid, races_completed=20,
    )


@skip_if_untrained
def test_the_trained_model_favours_the_stronger_driver():
    model = RaceModel.load(runs=4000)
    field = [_driver("Strong", 2.0), _driver("Mid", 9.0), _driver("Weak", 17.0)]
    probabilities = {r.driver: r.p_win for r in model.predict(field, seed=1)}

    assert probabilities["Strong"] > probabilities["Mid"] > probabilities["Weak"]


@skip_if_untrained
def test_the_trained_model_favours_a_better_grid_slot():
    model = RaceModel.load(runs=4000)
    field = [_driver("Pole", 6.0, grid=1), _driver("Back", 6.0, grid=18)]
    probabilities = {r.driver: r.p_win for r in model.predict(field, seed=1)}

    assert probabilities["Pole"] > probabilities["Back"]


@skip_if_untrained
def test_trained_probabilities_remain_coherent():
    model = RaceModel.load(runs=4000)
    field = [_driver("D{}".format(i), float(i), grid=i) for i in range(1, 21)]
    rows = model.predict(field, seed=1)

    assert sum(r.p_win for r in rows) == pytest.approx(1.0, abs=1e-9)
    assert all(r.p_win <= r.p_podium <= r.p_points for r in rows)
