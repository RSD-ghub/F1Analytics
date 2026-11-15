"""The qualifying-market backtest scores qualifying, not the race."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import backtest_quali_market as qm  # noqa: E402

from app.services.ingestion_client import QualiResult
from app.services.model import feature_names
from app.training.plackett_luce import RaceObservation

NAMES = feature_names(with_grid=False)
GAP = NAMES.index(qm.GAP_FEATURE)
FIELD = 14


def weekends(column, signal, seed=0, seasons=range(2016, 2022), per_season=16):
    """Qualifying decided by ``column`` (lower = faster) plus noise; the race
    order is random, so only a model of qualifying can score."""
    rng = np.random.default_rng(seed)
    obs, quali = [], []
    for season in seasons:
        for r in range(2, per_season + 2):
            x = rng.normal(size=(FIELD, len(NAMES)))
            drivers = ["D{}".format(i) for i in range(FIELD)]
            pace = -signal * x[:, column] + rng.gumbel(size=FIELD)
            for place, i in enumerate(np.argsort(-pace), start=1):
                quali.append(QualiResult(season=season, round=r, driver=drivers[i],
                                         position=place))
            obs.append(RaceObservation(x, list(rng.permutation(FIELD)), season=season,
                                       round_number=r, drivers=drivers))
    return qm.requalify(obs, quali)


def test_requalify_ranks_by_the_qualifying_classification():
    x = np.zeros((10, 1))
    drivers = ["D{}".format(i) for i in range(10)]
    obs = RaceObservation(x, list(range(10)), season=2025, round_number=3, drivers=drivers)
    quali = [QualiResult(season=2025, round=3, driver=d, position=10 - i)
             for i, d in enumerate(drivers)]
    assert qm.requalify([obs], quali)[0].order == list(range(9, -1, -1))
    assert qm.requalify([obs], quali[:5]) == []


def test_sampled_chances_add_up_to_the_places_on_offer():
    race = RaceObservation(np.random.default_rng(1).normal(size=(12, 2)), list(range(12)))
    chances = qm.place_chances(np.array([1.0, 0.0]), race, 1.0, runs=500)
    for name, k in qm.MARKETS:
        assert abs(chances[name].sum() - k) < 1e-9


def test_pace_from_recent_qualifying_shows_skill_but_no_edge_over_gap_only():
    summary = qm.run_observations(weekends(GAP, 2.5), [2019, 2020, 2021], 2016, 0.6)
    for name, _ in qm.MARKETS:
        assert summary["pooled"]["model"][name]["low"] > 0
        assert summary["pooled"]["gap_only"][name]["low"] > 0
    assert summary["verdict"].startswith("skilful, but not clearly better")


def test_another_feature_beats_gap_only_and_is_worth_building():
    other = 0 if GAP != 0 else 1
    summary = qm.run_observations(weekends(other, 2.5, seed=2), [2019, 2020, 2021], 2016, 0.6)
    assert summary["verdict"].startswith("worth building")


def test_noise_is_not_worth_building():
    summary = qm.run_observations(weekends(GAP, 0.0, seed=4), [2019, 2020, 2021], 2016, 0.6)
    assert summary["verdict"].startswith("not worth building")
