"""Shared test fixtures.

Unit tests build a model with explicit weights and identity scaling rather than
loading the fitted artifact. That keeps them testing *mechanics* — does better
form raise win probability, do probabilities sum to one, is sampling
reproducible — independently of whatever the current fit happens to be.
Retraining should not turn a mechanical test red.

The fitted artifact is exercised separately, in ``test_weights_artifact.py``.
"""

import pytest

from app.services.model import BASE_FEATURES, GRID_FEATURES, RaceModel

#: Signs matter and are physically meaningful: finishing-position features are
#: negative because a *lower* position is better, so a lower value must produce
#: a higher strength.
TEST_WEIGHTS = {
    "avg_finish_recent": -0.30,
    "avg_finish_season": -0.15,
    "team_avg_finish": -0.20,
    "circuit_avg_finish": -0.10,
    "points_per_race": 0.02,
    "dnf_rate": -0.10,
    "is_cold_start": -0.05,
    "avg_grid_recent": -0.20,
    "teammate_delta": -0.05,
    "positions_gained": 0.02,
    "recent_quali_gap_pct": -8.0,
    "quali_teammate_gap_pct": -4.0,
    "quali_gap_pct": -12.0,
    "practice_long_run_gap_pct": -10.0,
    "grid_legacy": -0.35,
    "grid_modern": -0.35,
}

#: Identity scaling, so a weight applies directly to the raw feature value and
#: the expected behaviour of each test is arithmetic anyone can follow.
IDENTITY_MEANS = {name: 0.0 for name in tuple(BASE_FEATURES) + GRID_FEATURES}
IDENTITY_STDS = {name: 1.0 for name in tuple(BASE_FEATURES) + GRID_FEATURES}


def make_model(noise_scale: float = 2.0, runs: int = 4000, **overrides) -> RaceModel:
    weights = dict(TEST_WEIGHTS, **overrides)
    return RaceModel(
        weights=weights,
        means=IDENTITY_MEANS,
        stds=IDENTITY_STDS,
        noise_scale=noise_scale,
        runs=runs,
    )


@pytest.fixture
def model() -> RaceModel:
    return make_model()
