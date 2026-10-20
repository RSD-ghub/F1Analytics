"""Driver and car ratings, learned race by race.

Every feature in the model describes a driver through averages of their
results — and a result is the driver and the car together. ``teammate_delta``
separates them a little, inside one team and one season. These ratings
separate them across a career: a driver's rating follows them between teams,
a car's follows the organisation between drivers.

**An Elo for a twenty-car race.** Each race's strength for a car is
``driver rating + car rating``. After the race both are nudged along the
gradient of the Plackett-Luce log-likelihood of the order that actually
happened — the same likelihood the race model is fitted by, with retirements
in every denominator and never ranked. A driver who beats what their ratings
predicted gains; one who falls short loses; how much is set by ``driver_k``
and ``car_k``.

**Pulled toward average.** At each new season a car's rating is shrunk toward
zero (``car_carry``) — it is a new car — and much more at a regulation reset
(``reset_carry``). A driver's rating is shrunk only slightly (``driver_carry``).
A newcomer starts at zero: average, until they show otherwise. That is the
shrinkage a hierarchical prior gives, in its simplest online form.

Computed over the full history in one pass (``ratings_by_race``), keyed by
race, so a forecast reads the ratings going *into* its race — nothing from
that race or after.
"""

from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

from app.services import team_lineage
from app.services.ingestion_client import RaceResult
from app.training import eras

DEFAULTS = {
    "driver_k": 0.05,
    "car_k": 0.08,
    "driver_carry": 0.9,
    "car_carry": 0.6,
    "reset_carry": 0.2,
}

Ratings = Tuple[Dict[str, float], Dict[str, float]]


def _gradient(strengths: np.ndarray, order) -> np.ndarray:
    """d log P(order) / d strength, under Plackett-Luce with censored retirements."""
    exponentials = np.exp(strengths - strengths.max())
    remaining = np.ones(len(strengths), dtype=bool)
    gradient = np.zeros(len(strengths))
    for chosen in order:
        pool = exponentials * remaining
        gradient -= pool / pool.sum()
        gradient[chosen] += 1.0
        remaining[chosen] = False
    return gradient


def ratings_by_race(
    results: Iterable[RaceResult], settings: Optional[Dict[str, float]] = None,
) -> Dict[Tuple[int, int], Ratings]:
    """(season, round) -> (driver ratings, car ratings) going into that race."""
    s = dict(DEFAULTS, **(settings or {}))
    by_race = defaultdict(list)
    for row in results:
        by_race[(row.season, row.round)].append(row)

    driver: Dict[str, float] = defaultdict(float)
    car: Dict[str, float] = defaultdict(float)
    out: Dict[Tuple[int, int], Ratings] = {}
    season = None
    for key in sorted(by_race):
        if season is not None and key[0] != season:
            reset = eras.era_for(key[0]) != eras.era_for(season)
            carry = s["reset_carry"] if reset else s["car_carry"]
            for name in car:
                car[name] *= carry
            for name in driver:
                driver[name] *= s["driver_carry"]
        season = key[0]
        out[key] = (dict(driver), dict(car))

        rows = by_race[key]
        cars = [team_lineage.lineage_of(row.team) for row in rows]
        strengths = np.array([driver[row.driver] + car[c] for row, c in zip(rows, cars)])
        finishers = sorted(
            (i for i, row in enumerate(rows) if row.classified),
            key=lambda i: rows[i].position)
        if len(finishers) < 3:
            continue
        gradient = _gradient(strengths, finishers)
        for i, row in enumerate(rows):
            driver[row.driver] += s["driver_k"] * gradient[i]
            car[cars[i]] += s["car_k"] * gradient[i]
    return out


def for_driver(ratings: Optional[Ratings], driver: str, team: str) -> Tuple[float, float]:
    """(driver rating, car rating); 0.0 — average — for anyone unrated."""
    if not ratings:
        return 0.0, 0.0
    drivers, cars = ratings
    return drivers.get(driver, 0.0), cars.get(team_lineage.lineage_of(team), 0.0)
