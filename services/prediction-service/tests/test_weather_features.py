"""The weather interactions are zero in a dry race and shaped as described."""

import numpy as np

from app.training import weather
from app.training.plackett_luce import RaceObservation


class _Row:
    def __init__(self, driver, season, rnd, grid, pos, classified=True):
        self.driver, self.season, self.round = driver, season, rnd
        self.grid_position, self.position, self.classified = grid, pos, classified


def test_wetness_prefers_tyres_over_the_gauge():
    stints = [
        {"season": 2024, "round": 1, "compound": "INTERMEDIATE", "laps": 30},
        {"season": 2024, "round": 1, "compound": "MEDIUM", "laps": 30},
    ]
    gauge = [{"season": 2024, "round": 1, "rainfall": False},
             {"season": 2024, "round": 2, "rainfall": True},
             {"season": 2024, "round": 2, "rainfall": False}]
    wet = weather.race_wetness(stints, gauge)
    assert wet[(2024, 1)] == 0.5   # tyres win over a dry gauge
    assert wet[(2024, 2)] == 0.5   # gauge fills in where no stints


def test_wet_skill_needs_both_wet_and_dry_history_and_is_shrunk():
    wetness = {(2023, 1): 0.0, (2023, 2): 0.9, (2023, 3): 0.0}
    rows = [_Row("A", 2023, 1, 5, 5), _Row("A", 2023, 2, 10, 2), _Row("A", 2023, 3, 5, 5)]
    records = weather.wet_records(rows, wetness)
    assert weather.wet_skill(records["A"], (2023, 2)) == 0.0          # no wet race yet
    skill = weather.wet_skill(records["A"], (2024, 1))
    assert skill == 8.0 * 1 / (1 + weather.PRIOR_RACES)               # +8 places, one wet race


def test_interactions_vanish_in_a_dry_race():
    names = ["avg_finish_recent", "grid_modern"]
    obs = RaceObservation(np.array([[3.0, 1.0], [8.0, 2.0]]), [0, 1], 2024, 5, drivers=["A", "B"])
    dry = weather.augment([obs], names, {}, {}, list(weather.CANDIDATES))[0]
    assert np.all(dry.features[:, 2:] == 0)
    wet = weather.augment([obs], names, {(2024, 5): 0.5}, {}, ["wet_x_grid", "wet_x_form"])[0]
    assert wet.features[:, 2].tolist() == [0.5, 1.0]
    assert wet.features[:, 3].tolist() == [1.5, 4.0]
