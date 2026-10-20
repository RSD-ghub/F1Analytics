"""Season-by-season backtests, with error bars.

The fixed split — fit on everything before 2022, choose on 2022-23, test on
2024-25 — scores a candidate on 46 test races. Twice now that has been too few:
team form helped 2022-23 and hurt 2024-25 by the same amount; a driver rating
did the reverse. Two seasons of one kind can outvote anything.

``rolling_origin`` asks the question more often. For each season, fit only on
the seasons before it and score that season — a forecast made the way it would
have been made at the time, nine times over instead of once. ``paired_bootstrap``
then says whether the per-race difference between two feature sets is
distinguishable from zero, because both were scored on the very same races.

A candidate earns its place when the interval clears zero, not when its mean
does.
"""

import copy
from typing import Dict, List, Sequence, Tuple

import numpy as np

from app.training import plackett_luce
from app.training.plackett_luce import RaceObservation


def _columns(observations: Sequence[RaceObservation], names: Sequence[str],
             keep: Sequence[str]) -> List[RaceObservation]:
    index = [list(names).index(name) for name in keep]
    out = []
    for observation in observations:
        sliced = copy.copy(observation)
        sliced.features = observation.features[:, index]
        out.append(sliced)
    return out


def rolling_origin(
    observations: Sequence[RaceObservation],
    names: Sequence[str],
    keep: Sequence[str],
    seasons: Sequence[int],
) -> Dict[Tuple[int, int], float]:
    """Per-race log-likelihood, each season scored by a fit on the ones before.

    ``observations`` must already be standardised over ``names``; ``keep`` is
    the feature set under test. Keyed by (season, round).
    """
    sliced = _columns(observations, names, keep)
    out: Dict[Tuple[int, int], float] = {}
    for season in seasons:
        train = [o for o in sliced if o.season < season]
        test = [o for o in sliced if o.season == season]
        if not train or not test:
            continue
        weights = plackett_luce.fit(train, keep)
        vector = np.array([weights[name] for name in keep])
        for observation in test:
            out[(observation.season, observation.round)] = plackett_luce.mean_log_likelihood(
                vector, [observation])
    return out


def paired_bootstrap(
    baseline: Dict[Tuple[int, int], float],
    candidate: Dict[Tuple[int, int], float],
    samples: int = 5000,
    seed: int = 0,
) -> Dict[str, float]:
    """Mean per-race gain of ``candidate`` over ``baseline``, with a 95% interval."""
    races = sorted(set(baseline) & set(candidate))
    gains = np.array([candidate[race] - baseline[race] for race in races])
    if not len(gains):
        return {"races": 0, "mean": 0.0, "low": 0.0, "high": 0.0, "better_share": 0.0}
    rng = np.random.default_rng(seed)
    means = rng.choice(gains, size=(samples, len(gains))).mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return {
        "races": len(gains),
        "mean": float(gains.mean()),
        "low": float(low),
        "high": float(high),
        "better_share": float((gains > 0).mean()),
    }


def by_season(per_race: Dict[Tuple[int, int], float]) -> Dict[int, float]:
    """Mean per-race log-likelihood for each season."""
    seasons: Dict[int, List[float]] = {}
    for (season, _), value in per_race.items():
        seasons.setdefault(season, []).append(value)
    return {season: float(np.mean(values)) for season, values in sorted(seasons.items())}
