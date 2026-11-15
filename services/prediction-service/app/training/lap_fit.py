"""Fitting and scoring the lap-by-lap simulator.

Two things are fitted against history and the rest chosen on validation
seasons:

* **Seconds per unit of strength** — regress each driver's measured race pace
  (``race_pace.fit_race``) on the strength the race model gave them before the
  race. The slope converts the model's scale into lap time; the residual spread
  is the race-day form a driver brings beyond what the model expected.
* **Race-day spread and passing threshold** — chosen on the validation seasons
  by the published-market score, the same objective the sampling temperature
  is chosen on, with the regression's spread as the starting point.

``score`` measures any engine the same way, so the simulator and the
Plackett-Luce sampler are compared on identical races and identical metrics.
"""

from typing import Callable, Dict, Sequence, Tuple

import numpy as np
from scipy import stats

from app.services.model import PODIUM_POSITIONS, POINTS_POSITIONS

#: The published markets' base rates, as in ``calibrate.evaluate_temperature``.
MARKET_BASE_RATES = ((1, 0.05), (PODIUM_POSITIONS, 0.15), (POINTS_POSITIONS, 0.50))


def fit_pace_scale(pairs: Sequence[Tuple[np.ndarray, np.ndarray]]) -> Tuple[float, float, int]:
    """Seconds per strength unit and race-day spread, from (strengths, pace) per race.

    Both are centred within each race, so only differences inside a race are
    used — the same thing the model's strengths mean. Gaps over four seconds a
    lap are damage or a wet race, not pace, and are left out. The spread is
    robust (median absolute deviation) so a handful of those does not set it.
    """
    xs, ys = [], []
    for strengths, pace in pairs:
        xs.extend(strengths - strengths.mean())
        ys.extend(pace - pace.mean())
    xs, ys = np.array(xs), np.array(ys)
    keep = np.abs(ys) < 4.0
    slope, intercept = np.polyfit(xs[keep], ys[keep], 1)
    residuals = ys[keep] - (slope * xs[keep] + intercept)
    spread = 1.4826 * float(np.median(np.abs(residuals - np.median(residuals))))
    return float(-slope), spread, int(keep.sum())


def score(
    sample: Callable[[object], Tuple[np.ndarray, np.ndarray]],
    races: Sequence,
    orderliness_every: int = 25,
) -> Dict[str, float]:
    """Published-market Brier, position score, and how orderly races come out.

    ``races`` are ``(key, drivers, actual_positions)``; ``sample(key)`` returns
    ``(positions, retired)``. Lower is better for ``market`` and ``positions``;
    ``orderliness_percentile`` should sit near 0.5 — where the real race's
    grid-to-finish rank correlation falls among the simulated ones.
    """
    brier = {cut: 0.0 for cut, _ in MARKET_BASE_RATES}
    cars = 0
    ranked = 0.0
    percentiles, simulated, observed = [], [], []
    for key, drivers, actual in races:
        positions, retired = sample(key)
        runs = positions.shape[0]
        n = len(drivers)
        actual = np.array(actual)
        finished = (actual >= 1) & (actual <= n)
        for cut, _ in MARKET_BASE_RATES:
            probability = ((positions <= cut) & ~retired).mean(axis=0)
            brier[cut] += float(((probability - ((actual <= cut) & finished)) ** 2).sum())
        cars += n
        # Ranked probability score over finishing places, retirement last.
        outcome = np.where(finished, actual, n + 1)
        places = np.arange(1, n + 1)
        for i in range(n):
            counts = np.bincount(positions[~retired[:, i], i], minlength=n + 1)[1:n + 1]
            cdf = np.cumsum(counts) / runs
            ranked += float(((cdf - (places >= outcome[i])) ** 2).sum()) / n
        grid = np.array([d.grid_position for d in drivers])
        if finished.sum() > 5:
            real = stats.spearmanr(grid[finished], actual[finished])[0]
            sims = [
                stats.spearmanr(grid[~retired[r]], positions[r][~retired[r]])[0]
                for r in range(0, runs, orderliness_every) if (~retired[r]).sum() > 5
            ]
            percentiles.append(float((np.array(sims) < real).mean()))
            simulated.append(float(np.mean(sims)))
            observed.append(float(real))
    return {
        "market": sum(brier[cut] / cars / base for cut, base in MARKET_BASE_RATES) / 3,
        "positions": ranked / cars,
        "orderliness_simulated": float(np.mean(simulated)) if simulated else 0.0,
        "orderliness_observed": float(np.mean(observed)) if observed else 0.0,
        "orderliness_percentile": float(np.mean(percentiles)) if percentiles else 0.5,
        "races": float(len(races)),
    }
