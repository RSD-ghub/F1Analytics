"""Model tests: probabilistic coherence, reproducibility, and directionality.

These assert the properties the product actually depends on. A model that emits
plausible-looking numbers which do not sum to one, or which shift between runs,
cannot support a calibration curve or an auditable track record — and both
failures are invisible in a single forecast.
"""

import numpy as np
import pytest

from app.models.schemas import DriverFeatures
from app.services.model import BASE_FEATURES, MODEL_VERSION, RaceModel
from tests.conftest import make_model


def _driver(name, form, team_form=None, dnf=0.0, grid=0, team="Team"):
    return DriverFeatures(
        driver=name,
        team=team,
        avg_finish_recent=form,
        avg_finish_season=form,
        team_avg_finish=team_form if team_form is not None else form,
        circuit_avg_finish=form,
        dnf_rate=dnf,
        grid_position=grid,
        races_completed=20,
    )


FIELD = [
    _driver("Fast", 2.0, team="Alpha"),
    _driver("Mid", 8.0, team="Beta"),
    _driver("Slow", 15.0, team="Gamma"),
    _driver("Backmarker", 18.0, team="Delta"),
]


# ── Probabilistic coherence ──────────────────────────────────────────────────


def test_win_probabilities_sum_to_one(model):
    """Exactly one driver wins each simulated race."""
    result = model.predict(FIELD, seed=1)
    assert sum(r.p_win for r in result) == pytest.approx(1.0, abs=1e-9)


def test_podium_probabilities_sum_to_three(model):
    result = model.predict(FIELD, seed=1)
    # Only 4 drivers, so all but one are on the podium every time.
    assert sum(r.p_podium for r in result) == pytest.approx(3.0, abs=1e-9)


def test_probabilities_are_ordered_within_a_driver(model):
    """p_win <= p_podium <= p_points is structurally required."""
    for row in model.predict(FIELD, seed=1):
        assert row.p_win <= row.p_podium <= row.p_points


def test_all_probabilities_are_in_range(model):
    for row in model.predict(FIELD, seed=1):
        assert 0.0 <= row.p_win <= 1.0
        assert 0.0 <= row.p_podium <= 1.0
        assert 0.0 <= row.p_points <= 1.0


def test_empty_field_returns_nothing(model):
    assert model.predict([], seed=1) == []


# ── Directionality ───────────────────────────────────────────────────────────


def test_better_form_yields_a_higher_win_probability(model):
    result = {r.driver: r.p_win for r in model.predict(FIELD, seed=1)}
    assert result["Fast"] > result["Mid"] > result["Slow"] > result["Backmarker"]


def test_pole_position_helps_in_the_post_quali_window(model):
    """The sign on the grid weight — inverting it would confidently reverse the grid."""
    from_pole = _driver("X", 8.0, grid=1)
    from_the_back = _driver("Y", 8.0, grid=20)
    field = [from_pole, from_the_back, _driver("Z", 8.0, grid=10)]

    result = {r.driver: r.p_win for r in model.predict(field, seed=1)}
    assert result["X"] > result["Z"] > result["Y"]


def test_unreliability_reduces_win_probability(model):
    """A fast but fragile car must not get an unrealistic p_win."""
    reliable = _driver("Reliable", 3.0, dnf=0.0)
    fragile = _driver("Fragile", 3.0, dnf=0.5)
    result = {r.driver: r.p_win for r in model.predict([reliable, fragile], seed=1)}

    assert result["Reliable"] > result["Fragile"]


def test_identical_drivers_get_indistinguishable_probabilities(model):
    """No hidden positional bias from input ordering."""
    field = [_driver("A", 5.0), _driver("B", 5.0), _driver("C", 5.0)]
    result = {r.driver: r.p_win for r in model.predict(field, seed=7)}

    assert result["A"] == pytest.approx(1 / 3, abs=0.03)
    assert result["B"] == pytest.approx(1 / 3, abs=0.03)
    assert result["C"] == pytest.approx(1 / 3, abs=0.03)


# ── Calibration knob ─────────────────────────────────────────────────────────


def test_higher_noise_flattens_probabilities_toward_uniform():
    """noise_scale is the calibration parameter; it must actually do that."""
    confident = make_model(noise_scale=0.5, runs=4000).predict(FIELD, seed=1)
    uncertain = make_model(noise_scale=4.0, runs=4000).predict(FIELD, seed=1)

    top_confident = max(r.p_win for r in confident)
    top_uncertain = max(r.p_win for r in uncertain)
    assert top_confident > top_uncertain


def test_very_high_noise_approaches_a_coin_flip():
    result = make_model(noise_scale=50.0, runs=4000).predict(FIELD, seed=3)
    for row in result:
        assert row.p_win == pytest.approx(0.25, abs=0.05)


# ── Reproducibility ──────────────────────────────────────────────────────────


def test_same_seed_produces_identical_output(model):
    """Required for a locked prediction to be auditable."""
    first = model.predict(FIELD, seed=42)
    second = model.predict(FIELD, seed=42)

    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_different_seeds_produce_different_samples(model):
    first = model.predict(FIELD, seed=1)
    second = model.predict(FIELD, seed=2)

    assert [r.p_win for r in first] != [r.p_win for r in second]


def test_model_version_is_recorded():
    assert make_model().version == MODEL_VERSION


def test_parameters_are_reported_for_audit():
    """Every fitted weight is recorded on the prediction, not just the config."""
    params = make_model(noise_scale=1.5).parameters()

    assert params["noise_scale"] == 1.5
    assert params["grid_modern"] == -0.35
    assert params["avg_finish_recent"] == -0.30
    assert set(BASE_FEATURES) <= set(params)


# ── Sampling mechanics ───────────────────────────────────────────────────────


def test_every_sampled_order_is_a_complete_ranking(model):
    """Retirements must not punch holes in the finishing order."""
    field = [_driver("A", 5.0, dnf=0.9), _driver("B", 5.0, dnf=0.9), _driver("C", 5.0)]
    orders = model.sample_orders(field, seed=1, runs=200)

    assert orders.shape == (200, 3)
    for row in orders:
        assert sorted(row.tolist()) == [1, 2, 3]


def test_sampled_positions_stay_within_the_field(model):
    orders = model.sample_orders(FIELD, seed=1, runs=200)
    assert orders.min() >= 1
    assert orders.max() <= len(FIELD)
