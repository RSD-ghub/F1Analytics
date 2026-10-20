"""One race, simulated thousands of times on the confirmed grid.

The forecast windows publish three numbers per driver. A simulation has far more
to say than that: every run is a whole race, so the runs together give each
driver's full spread of finishing positions — how often they retire, how often
they finish fourth, whether their result is a narrow peak or a wide smear. This
module keeps all of it.

The championship view (``simulation.py``) simulates a season; this simulates one
race and keeps the detail a season total throws away.
"""

from typing import Dict, List, Optional, Sequence

import numpy as np

from app.models.schemas import DriverFeatures, DriverProbability, SimulatedDriver
from app.services.model import PODIUM_POSITIONS, POINTS_POSITIONS, RaceModel


def simulate(
    model: RaceModel,
    drivers: Sequence[DriverFeatures],
    seed: int,
    runs: int,
    points_positions: int = POINTS_POSITIONS,
    baseline: Optional[Sequence[DriverProbability]] = None,
    outcomes=None,
) -> List[SimulatedDriver]:
    """Simulate ``runs`` races and summarise each driver's outcomes.

    ``outcomes`` is ``(positions, retired)`` already run by another engine —
    the lap-by-lap simulator; without it they are sampled from ``model``.
    Ordered by win chance, then expected position, like the forecasts.
    """
    if not drivers:
        return []
    positions, retired = outcomes or model.sample_outcomes(drivers, seed=seed, runs=runs)
    runs = positions.shape[0]
    field = len(drivers)
    by_driver: Dict[str, DriverProbability] = {
        row.driver: row for row in baseline or []
    }

    out: List[SimulatedDriver] = []
    for index, driver in enumerate(drivers):
        finished = ~retired[:, index]
        column = positions[:, index]
        # Retired cars sit behind every finisher in ``positions``. They are
        # left out of the position counts and out of every market: a car that
        # retires is not classified fifth because only four others finished.
        counts = np.bincount(column[finished] - 1, minlength=field)[:field]
        out.append(SimulatedDriver(
            driver=driver.driver,
            team=driver.team,
            grid_position=driver.grid_position,
            p_win=float(((column == 1) & finished).sum() / runs),
            p_podium=float(((column <= PODIUM_POSITIONS) & finished).sum() / runs),
            p_points=float(((column <= points_positions) & finished).sum() / runs),
            p_dnf=float((~finished).sum() / runs),
            expected_position=float(column.mean()),
            position_probabilities=[float(c / runs) for c in counts],
            baseline=by_driver.get(driver.driver),
        ))
    out.sort(key=lambda d: (-d.p_win, d.expected_position, d.driver))
    return out
