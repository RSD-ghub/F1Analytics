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

``scripts/backtest.py`` runs the full-pipeline version (``rolling_folds``):
each fold also re-derives its own feature scaling and era weights, scores
improvement over uniform with an interval per season, and compares the
linear model with the network race by race.
"""

import copy
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

from app.training import eras, plackett_luce
from app.training.plackett_luce import RaceObservation, negative_log_likelihood


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


#: Resamples for every interval. Enough that the interval edges are stable to
#: the second decimal of a percentage.
BOOTSTRAP_RESAMPLES = 2000

#: Fixed so the same data always reports the same interval.
BOOTSTRAP_SEED = 20261019


def race_log_likelihood(weights: np.ndarray, race: RaceObservation) -> float:
    """One race's log-likelihood under a linear model, unweighted, unpenalised."""
    total, _ = negative_log_likelihood(
        weights, [race], l2=0.0, use_sample_weights=False
    )
    return -total


def uniform_log_likelihood(race: RaceObservation) -> float:
    """One race's log-likelihood with every driver equally likely at each step."""
    pool = race.field_size
    total = 0.0
    for _ in race.order:
        total -= np.log(pool)
        pool -= 1
    return total


def _resample(size: int, resamples: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, size, size=(resamples, size))


def _quantiles(samples: np.ndarray, level: float) -> Tuple[float, float]:
    tail = (1.0 - level) / 2.0
    low, high = np.quantile(samples, [tail, 1.0 - tail])
    return float(low), float(high)


def bootstrap_interval(
    values: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> Tuple[float, float, float]:
    """Mean of ``values`` and a percentile bootstrap interval around it."""
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return 0.0, 0.0, 0.0
    means = array[_resample(array.size, resamples, seed)].mean(axis=1)
    low, high = _quantiles(means, level)
    return float(array.mean()), low, high


def improvement_interval(
    model: Sequence[float],
    uniform: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> Tuple[float, float, float]:
    """Improvement over uniform (the headline percentage), with its interval.

    Resamples races and recomputes the ratio each time, so the interval
    reflects uncertainty in both the model's score and the baseline's.
    """
    m = np.asarray(model, dtype=float)
    u = np.asarray(uniform, dtype=float)
    if m.size == 0:
        return 0.0, 0.0, 0.0
    picks = _resample(m.size, resamples, seed)
    m_means = m[picks].mean(axis=1)
    u_means = u[picks].mean(axis=1)
    low, high = _quantiles((m_means - u_means) / np.abs(u_means), level)
    point = (m.mean() - u.mean()) / abs(u.mean())
    return float(point), low, high


def paired_difference(
    challenger: Sequence[float],
    champion: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> Dict[str, float]:
    """Challenger minus champion, race by race, with a paired interval.

    ``share_better`` is the fraction of resamples in which the challenger came
    out ahead: a one-number answer to "how sure are we it is better".
    """
    a = np.asarray(challenger, dtype=float)
    b = np.asarray(champion, dtype=float)
    if a.shape != b.shape:
        raise ValueError("paired scores must cover the same races")
    differences = a - b
    if differences.size == 0:
        return {"mean": 0.0, "low": 0.0, "high": 0.0, "share_better": 0.0, "races": 0}
    means = differences[_resample(differences.size, resamples, seed)].mean(axis=1)
    low, high = _quantiles(means, level)
    return {
        "mean": float(differences.mean()),
        "low": low,
        "high": high,
        "share_better": float((means > 0).mean()),
        "races": int(differences.size),
    }


@dataclass
class FoldResult:
    """One held-out season: per-race scores for every model, in race order."""

    season: int
    train_races: int
    rounds: List[int] = field(default_factory=list)
    uniform: List[float] = field(default_factory=list)
    scores: Dict[str, List[float]] = field(default_factory=dict)


#: A model under test: given the fold's standardised training races and the
#: feature names, return a function scoring one standardised test race.
ModelFactory = Callable[
    [List[RaceObservation], Sequence[str]], Callable[[RaceObservation], float]
]


def rolling_folds(
    observations: Sequence[RaceObservation],
    names: Sequence[str],
    test_seasons: Sequence[int],
    models: Dict[str, ModelFactory],
    decay: float = 0.6,
    from_season: int = 0,
) -> List[FoldResult]:
    """Fit every model on seasons before S and score it on S, for each S.

    ``observations`` must hold *raw* (unstandardised) features; each fold
    computes its own scaling from its own training races and applies it to the
    test season. Grid columns are scaled but never centred, as in training.
    Era weights are recomputed with S as the target season.
    """
    results: List[FoldResult] = []
    for season in sorted(test_seasons):
        train_raw = [o for o in observations if from_season <= o.season < season]
        test_raw = [o for o in observations if o.season == season]
        if not train_raw or not test_raw:
            continue

        means, stds = _scaling(train_raw, names)
        train = [
            _scaled(o, means, stds, eras.sample_weight(o.season, season, decay=decay))
            for o in train_raw
        ]
        test = [_scaled(o, means, stds, 1.0) for o in test_raw]

        fold = FoldResult(
            season=season,
            train_races=len(train),
            rounds=[o.round for o in test],
            uniform=[uniform_log_likelihood(o) for o in test],
        )
        for label, factory in models.items():
            scorer = factory(train, names)
            fold.scores[label] = [scorer(o) for o in test]
        results.append(fold)
    return results


def pooled(folds: Sequence[FoldResult], label: str) -> Tuple[List[float], List[float]]:
    """Every test race across every fold: (model scores, uniform scores)."""
    model: List[float] = []
    uniform: List[float] = []
    for fold in folds:
        model.extend(fold.scores[label])
        uniform.extend(fold.uniform)
    return model, uniform


def _scaling(
    observations: Sequence[RaceObservation], names: Sequence[str]
) -> Tuple[np.ndarray, np.ndarray]:
    """Same rule as ``dataset.standardise``, without mutating its input."""
    stacked = np.vstack([o.features for o in observations])
    means = np.zeros(len(names))
    stds = np.ones(len(names))
    for index, name in enumerate(names):
        column = stacked[:, index]
        if name.startswith("grid_"):
            nonzero = column[column != 0]
            spread = float(nonzero.std()) if nonzero.size else 1.0
        else:
            means[index] = float(column.mean())
            spread = float(column.std())
        stds[index] = spread if spread > 1e-9 else 1.0
    return means, stds


def _scaled(
    observation: RaceObservation, means: np.ndarray, stds: np.ndarray, weight: float
) -> RaceObservation:
    return RaceObservation(
        features=(observation.features - means) / stds,
        order=observation.order,
        season=observation.season,
        round_number=observation.round,
        weight=weight,
    )


def verdict(paired: Dict[str, float]) -> str:
    """Plain-language reading of a paired comparison."""
    if paired["low"] > 0:
        return "challenger is better: the whole interval is above zero"
    if paired["high"] < 0:
        return "challenger is worse: the whole interval is below zero"
    return "no measurable difference: the interval includes zero"
