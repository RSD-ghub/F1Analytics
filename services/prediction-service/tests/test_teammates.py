"""Team-mate head-to-heads in the race simulation."""

import numpy as np

from app.models.schemas import DriverFeatures
from app.services.race_simulation import _teammate, simulate


def _drivers():
    return [DriverFeatures(driver="A", team="Red"), DriverFeatures(driver="B", team="Red"),
            DriverFeatures(driver="C", team="Blue"), DriverFeatures(driver="D", team="Unknown")]


def test_head_to_head_counts_retirements_behind_finishers():
    drivers = _drivers()
    positions = np.array([[1, 2, 3, 4], [2, 1, 3, 4], [4, 1, 2, 3], [3, 4, 1, 2]])
    retired = np.array([[False] * 4, [False] * 4,
                        [True, False, False, False],   # A out: B beats A
                        [True, True, False, False]])   # both out: neither
    a = _teammate(drivers, 0, positions, retired)
    b = _teammate(drivers, 1, positions, retired)
    assert a == {"teammate": "B", "p_beats_teammate": 0.25}
    assert b == {"teammate": "A", "p_beats_teammate": 0.5}
    assert _teammate(drivers, 2, positions, retired) == {}  # alone in the team
    assert _teammate(drivers, 3, positions, retired) == {}  # team unknown


def test_simulate_carries_it_through():
    class _Model:
        def sample_outcomes(self, drivers, seed, runs):
            return (np.array([[1, 2, 3, 4], [2, 1, 3, 4]]), np.zeros((2, 4), bool))

    rows = {r.driver: r for r in simulate(_Model(), _drivers(), seed=1, runs=2)}
    assert rows["A"].teammate == "B" and rows["A"].p_beats_teammate == 0.5
    assert rows["C"].teammate is None and rows["C"].p_beats_teammate is None
