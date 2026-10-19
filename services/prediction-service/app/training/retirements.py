"""How retirements cluster: within a race, and within a team.

The simulator draws each car's retirement from its own historical rate. Drawn
independently, that misses two things the record shows plainly (2014-2025, 252
races):

* **Some races eat cars.** The number of retirements per race varies about 1.4×
  more than independent draws allow — a first-lap pile-up, a wet race, a
  street circuit with walls.
* **Team-mates fail together.** Both cars of a team retire about 1.5× as often
  as independent draws allow — a shared component batch, a strategy that put
  both in the same trouble, one car collecting the other.

Both are modelled as gamma-distributed multipliers on each car's retirement
hazard, mean 1 so the average retirement rate is untouched: one per race
(shape ``race_k``), one per team per race (shape ``team_k``). A smaller shape is
a wider spread, so more clustering.

They are fitted by matching those two moments directly, over every race in the
training span, rather than by tuning against a few dozen held-out races: with
one or two retirements per car per season, a score over 46 races cannot tell
these settings apart, and the moments use every race there is.
"""

from collections import defaultdict
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from app.services.ingestion_client import RaceResult


def clustering_moments(results: Sequence[RaceResult]) -> Dict[str, float]:
    """The record's retirement clustering, as the two ratios the fit matches."""
    by_race = defaultdict(list)
    for row in results:
        by_race[(row.season, row.round)].append(row)
    races = [rows for rows in by_race.values() if len(rows) >= 6]
    if not races:
        return {}

    cars = sum(len(rows) for rows in races)
    retired = sum(row.retired for rows in races for row in rows)
    p = retired / cars
    counts = np.array([sum(row.retired for row in rows) for rows in races], dtype=float)
    binomial_variance = float(np.mean([len(rows) * p * (1 - p) for rows in races]))

    pairs = both = 0
    car_pairs = team_pairs = 0.0
    for rows in races:
        teams = defaultdict(list)
        for row in rows:
            teams[row.team].append(row.retired)
        n = len(rows)
        car_pairs += n * (n - 1)
        for flags in teams.values():
            if len(flags) == 2:
                pairs += 1
                both += flags[0] and flags[1]
                team_pairs += 2
    return {
        "races": float(len(races)),
        "retirement_rate": p,
        "count_variance": float(counts.var()),
        "binomial_variance": binomial_variance,
        "dispersion": float(counts.var()) / binomial_variance if binomial_variance else 1.0,
        "teammate_both_rate": both / pairs if pairs else 0.0,
        "teammate_ratio": (both / pairs) / (p * p) if pairs and p else 1.0,
        "car_pairs_per_race": car_pairs / len(races),
        "team_pairs_per_race": team_pairs / len(races),
    }


def fit(results: Sequence[RaceResult]) -> Tuple[Optional[float], Optional[float], Dict]:
    """Gamma shapes ``(race_k, team_k)`` matching the record's clustering.

    The simulator draws a retirement with probability ``p·m`` (capped), so with independent race and team multipliers of variance
    ``a = 1/race_k`` and ``1/team_k``:

    * two cars in one race co-retire at ``p²(1 + a)``;
    * team-mates co-retire at ``p²(1 + a + b)``, with ``b = (1 + a)/team_k``.

    The excess variance of the per-race count is the sum of those excesses over
    every pair of cars, which with the team-mate ratio gives two equations in
    ``a`` and ``b``. ``None`` for a shape means no clustering of that kind was
    found: draw that multiplier as exactly 1.
    """
    moments = clustering_moments(results)
    if not moments:
        return None, None, moments
    p = moments["retirement_rate"]
    excess = (moments["count_variance"] - moments["binomial_variance"]) / (p * p)
    ratio = moments["teammate_ratio"] - 1.0
    car_pairs = moments["car_pairs_per_race"]
    team_pairs = moments["team_pairs_per_race"]
    # car_pairs·a + team_pairs·b = excess ;  a + b = ratio
    if car_pairs == team_pairs:
        return None, None, moments
    a = (excess - team_pairs * ratio) / (car_pairs - team_pairs)
    a = max(a, 0.0)
    b = max(ratio - a, 0.0)
    race_k = 1.0 / a if a > 1e-6 else None
    team_k = (1.0 + a) / b if b > 1e-6 else None
    return race_k, team_k, moments
