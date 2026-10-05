"""What a promotion writes to disk.

This path had never executed. The gate had never passed, so the one function
that replaces the serving model was dead code that nobody had reason to read —
and it was wrong in a way that would not have raised, logged, or shown up in any
forecast that looked obviously broken.

It copied the outgoing artifact and overwrote three keys: weights, means, stds.
Everything else was inherited, including ``pre_quali_weights`` and
``noise_scale``. A promotion would therefore have served the challenger's
post-qualifying weights beside the *previous* model's pre-qualifying weights, at
the previous model's calibration. The pre-quali window would have been
predicting with weights belonging to a model that no longer existed.

These tests are the reason that cannot ship.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import retrain  # noqa: E402
from app.training.promotion import PromotionDecision  # noqa: E402


class _Args:
    decay = 0.6
    target_season = 2026


OUTGOING = {
    "version": "race-plackett-luce-v3",
    "weights": {"a": 0.1, "b": 0.2},
    "feature_means": {"a": 1.0, "b": 1.0},
    "feature_stds": {"a": 2.0, "b": 2.0},
    "pre_quali_weights": {"a": 0.9},
    "noise_scale": 0.3,
    "pre_quali_noise_scale": 0.3,
    "training_seasons": [2010, 2011],
    "era_decay": 0.5,
    "something_unrelated": "kept",
}

CHALLENGER = {
    "weights": {"a": 1.5, "b": -0.4},
    "means": {"a": 3.0, "b": 3.0},
    "stds": {"a": 4.0, "b": 4.0},
    "train_seasons": [2014, 2015],
    "train": {"races": 2},
    "test": {"races": 1},
}

PRE_QUALI = {
    "weights": {"a": 1.1},
    "means": {"a": 3.0},
    "stds": {"a": 4.0},
    "train_seasons": [2014, 2015],
    "train": {"races": 2},
    "test": {"races": 1},
}

TEMPERATURES = {"post_quali": (0.45, {0.3: 0.5, 0.45: 0.4}),
                "pre_quali": (0.60, {0.3: 0.6, 0.60: 0.5})}

DECISION = PromotionDecision(
    promote=True, reason="better", champion_score=-41.7, challenger_score=-41.5,
    baseline_score=-44.8, relative_gain=0.07, evaluation_races=15,
    evaluated_seasons=[2026], champion_scale=1.1, challenger_scale=0.8,
    scale_fitted_on=[2022, 2023],
)


@pytest.fixture
def promoted(tmp_path, monkeypatch):
    path = tmp_path / "model_weights.json"
    monkeypatch.setattr(retrain, "ARTIFACT", str(path))
    retrain._write_promoted(
        OUTGOING, {"post_quali": CHALLENGER, "pre_quali": PRE_QUALI},
        ["a", "b"], [2026], [2022, 2023], TEMPERATURES, DECISION, _Args(),
    )
    return json.loads(path.read_text())


def test_the_pre_quali_weights_belong_to_the_promoted_model(promoted):
    """The bug. Inheriting these would pair one model's pre-quali weights with
    another model's post-quali weights, and serve the result as one model."""
    assert promoted["pre_quali_weights"] == PRE_QUALI["weights"]
    assert promoted["pre_quali_weights"] != OUTGOING["pre_quali_weights"]


def test_the_calibration_belongs_to_the_promoted_weights(promoted):
    """A temperature is fitted *for* a weight vector. Carried across, it
    describes a model that is no longer being served."""
    assert promoted["noise_scale"] == 0.45
    assert promoted["pre_quali_noise_scale"] == 0.60
    assert promoted["noise_scale"] != OUTGOING["noise_scale"]


def test_every_weight_derived_field_is_rewritten(promoted):
    assert promoted["weights"] == CHALLENGER["weights"]
    assert promoted["feature_means"] == CHALLENGER["means"]
    assert promoted["feature_stds"] == CHALLENGER["stds"]
    assert promoted["training_seasons"] == CHALLENGER["train_seasons"]
    assert promoted["era_decay"] == _Args.decay


def test_no_stale_metric_survives_from_the_outgoing_model(promoted):
    """Metrics describe a fit. Keeping the old ones would publish the previous
    model's held-out score beside the new model's weights."""
    assert promoted["metrics"]["post_quali"]["test"] == CHALLENGER["test"]
    assert promoted["metrics"]["pre_quali"]["test"] == PRE_QUALI["test"]


def test_the_decision_and_its_lineage_are_recorded(promoted):
    """Every published prediction has to stay reproducible, which means knowing
    which model made it and what justified the swap."""
    assert promoted["previous_version"] == OUTGOING["version"]
    assert promoted["promoted_on_seasons"] == [2026]
    assert promoted["promotion"]["relative_gain"] == pytest.approx(0.07)
    assert promoted["promotion"]["champion_scale"] == 1.1
    assert promoted["promotion"]["challenger_scale"] == 0.8


def test_promoted_on_seasons_is_what_stops_the_next_cycle_reusing_them(promoted):
    """The anti-holdout-burn record. Without it every cycle re-tests on the same
    races until one of them gets lucky."""
    assert promoted["promoted_on_seasons"] == DECISION.evaluated_seasons


def test_unrelated_fields_are_carried_forward(promoted):
    """Rewriting is targeted at what depends on the weights; the artifact is
    not rebuilt from nothing, so anything else survives a promotion."""
    assert promoted["something_unrelated"] == "kept"


# ── Each promotion is a named model ──────────────────────────────────────────


def test_a_promoted_model_does_not_inherit_its_predecessors_name():
    """The defect this prevents: two weight sets under one name.

    Predictions record only the version string, so sharing one makes it
    impossible to say afterwards which weights produced which forecast — and
    the track record, which scores versions separately precisely so it does not
    average two different models, would average them.
    """
    assert retrain.next_version("race-plackett-luce-v4") == "race-plackett-luce-v4+r2"


def test_revisions_keep_counting():
    assert retrain.next_version("race-plackett-luce-v4+r2") == "race-plackett-luce-v4+r3"
    assert retrain.next_version("race-plackett-luce-v4+r9") == "race-plackett-luce-v4+r10"


def test_a_renamed_family_starts_its_own_line():
    """A new architecture is already a distinct name; it does not inherit the
    revision count of the one it replaces."""
    assert retrain.next_version("race-plackett-luce-v3", family="race-plackett-luce-v4") \
        == "race-plackett-luce-v4"


def test_a_missing_or_unparseable_predecessor_still_yields_a_new_name():
    """Never return the incumbent's name — that is the failure being fixed."""
    for previous in (None, "", "race-plackett-luce-v4+rabc"):
        assert retrain.next_version(previous) != previous


def test_the_written_artifact_carries_the_new_name_and_names_its_parent(promoted):
    """The fixture's outgoing model is a v3, so this also covers the family
    rename: the promoted model takes the current family name outright rather
    than a revision of the old one. What matters either way is that it does not
    reuse its predecessor's name."""
    assert promoted["version"] == "race-plackett-luce-v4"
    assert promoted["previous_version"] == "race-plackett-luce-v3"
    assert promoted["version"] != promoted["previous_version"]
