"""Retirements cluster by race and by team, without moving the average rate."""

import numpy as np

from app.models.schemas import DriverFeatures
from app.services.ingestion_client import RaceResult
from app.services.model import RaceModel
from app.training import retirements

RATE = 0.15


def field():
    return [DriverFeatures(driver="D{}".format(i), team="T{}".format(i // 2),
                           dnf_rate=RATE, grid_position=i + 1) for i in range(20)]


def model(race_k=None, team_k=None):
    m = RaceModel(weights={"grid_modern": -0.5}, noise_scale=0.3)
    m.retirement_race_k, m.retirement_team_k = race_k, team_k
    return m


def test_without_clustering_the_draws_are_exactly_what_they_were():
    """A locked forecast's numbers must not move because the option exists."""
    a, ra = model().sample_outcomes(field(), seed=3, runs=500)
    b, rb = RaceModel(weights={"grid_modern": -0.5}, noise_scale=0.3).sample_outcomes(
        field(), seed=3, runs=500)
    assert np.array_equal(a, b) and np.array_equal(ra, rb)


def test_clustering_keeps_the_rate_and_widens_the_spread():
    _, independent = model().sample_outcomes(field(), seed=1, runs=60000)
    _, clustered = model(race_k=8.0, team_k=2.5).sample_outcomes(field(), seed=1, runs=60000)
    assert abs(clustered.mean() - independent.mean()) < 0.005
    assert clustered.sum(axis=1).var() > 1.3 * independent.sum(axis=1).var()
    both = np.mean([(clustered[:, 2 * t] & clustered[:, 2 * t + 1]).mean() for t in range(10)])
    assert both / RATE ** 2 > 1.3


def synthetic_history(race_k, team_k, races=3000, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(races):
        m_race = rng.gamma(race_k, 1 / race_k)
        for t in range(10):
            m_team = rng.gamma(team_k, 1 / team_k)
            for d in range(2):
                retired = rng.random() < min(RATE * m_race * m_team, 0.95)
                rows.append(RaceResult(
                    season=2000 + r // 20, round=r % 20 + 1,
                    driver="D{}{}".format(t, d), team="T{}".format(t),
                    position=20 if retired else 1,
                    classified_position="R" if retired else "1"))
    return rows


def test_the_fit_recovers_the_shapes_it_was_generated_with():
    race_k, team_k, moments = retirements.fit(synthetic_history(10.0, 3.0))
    assert 6.0 < race_k < 16.0
    assert 2.2 < team_k < 4.0


def test_independent_history_finds_no_team_clustering():
    _, team_k, moments = retirements.fit(synthetic_history(1e6, 1e6))
    assert abs(moments["teammate_ratio"] - 1.0) < 0.1
    assert team_k is None or team_k > 10
