"""Post-hoc temperature calibration.

Maximum likelihood fits the weights to explain *whole finishing orders*. That is
the right objective for discrimination — it is what made the win market go from
worse-than-guessing to genuinely skilful — but it is not the same objective as
"when the model says 30%, does it happen 30% of the time".

The two come apart because most of a finishing order's information sits in the
midfield, while the product's published claims are about the top of it. So a
single scalar temperature is fitted afterwards to sharpen or flatten the
probabilities, chosen to minimise calibration error on the markets that are
actually published.

**On a separate split.** The temperature is fitted on validation seasons that are
neither in the weight fitting nor in the final test. Tuning it on the test
seasons would produce a calibration number that describes the tuning rather than
the model, which is precisely the sort of self-flattery the track record exists
to prevent.

Only one parameter is fitted here, so the risk of overfitting the validation set
is small — but the split is what makes the reported test calibration honest.
"""

import logging
from typing import Dict, List, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Candidate temperatures. >1 flattens toward uniform, <1 sharpens.
CANDIDATES = (0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.60, 0.80, 1.0, 1.4, 2.0)

PODIUM_POSITIONS = 3
POINTS_POSITIONS = 10


def expected_calibration_error(
    samples: Sequence[Tuple[float, bool]], buckets: int = 10
) -> float:
    """Mean |predicted - observed| across probability bands, weighted by count."""
    if not samples:
        return 0.0
    collected: Dict[int, List[Tuple[float, bool]]] = {}
    for probability, happened in samples:
        index = min(int(probability * buckets), buckets - 1)
        collected.setdefault(index, []).append((probability, happened))

    total = 0.0
    for entries in collected.values():
        predicted = sum(p for p, _ in entries) / len(entries)
        observed = sum(1 for _, o in entries if o) / len(entries)
        total += len(entries) * abs(predicted - observed)
    return total / len(samples)


def _market_samples(
    probabilities: np.ndarray, actual_positions: Sequence[int], cutoff: int
) -> List[Tuple[float, bool]]:
    return [
        (float(probabilities[index]), 1 <= position <= cutoff)
        for index, position in enumerate(actual_positions)
    ]


def _brier(samples: Sequence[Tuple[float, bool]]) -> float:
    if not samples:
        return 0.0
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in samples) / len(samples)


def evaluate_temperature(
    model,
    races: Sequence,
    temperature: float,
    runs: int = 4000,
) -> float:
    """Mean Brier score across all three published markets. Lower is better.

    Brier rather than calibration error, and all three markets rather than two.
    An earlier version minimised calibration error over podium and points only,
    and it was a trap: it chose a temperature that gave excellent podium numbers
    while driving win-market skill to *minus 34%* — far worse than guessing. The
    objective simply had no term that noticed.

    Brier is a proper scoring rule, so it rewards calibration and discrimination
    together and cannot be improved by sacrificing one market to flatter another.
    """
    original = (model.noise_scale, model.pre_quali_noise_scale)
    # Both must move: sample_orders picks one based on whether a grid is
    # present, so setting only the post-quali attribute made the pre-quali
    # sweep a silent no-op that returned an identical score for every candidate.
    model.noise_scale = temperature
    model.pre_quali_noise_scale = temperature
    try:
        win: List[Tuple[float, bool]] = []
        podium: List[Tuple[float, bool]] = []
        points: List[Tuple[float, bool]] = []
        for drivers, actual_positions, seed in races:
            orders = model.sample_orders(drivers, seed=seed, runs=runs)
            win.extend(_market_samples(
                (orders == 1).sum(axis=0) / runs, actual_positions, 1))
            podium.extend(_market_samples(
                (orders <= PODIUM_POSITIONS).sum(axis=0) / runs,
                actual_positions, PODIUM_POSITIONS))
            points.extend(_market_samples(
                (orders <= POINTS_POSITIONS).sum(axis=0) / runs,
                actual_positions, POINTS_POSITIONS))
        # Normalised by each market's base rate so the rare win market is not
        # swamped by the common points market.
        return (
            _brier(win) / 0.05 + _brier(podium) / 0.15 + _brier(points) / 0.50
        ) / 3
    finally:
        model.noise_scale, model.pre_quali_noise_scale = original


def fit_temperature(
    model,
    races: Sequence,
    runs: int = 4000,
) -> Tuple[float, Dict[float, float]]:
    """Pick the temperature minimising calibration error on the validation split.

    Returns the chosen value and the full sweep, so the choice is inspectable
    rather than an unexplained constant in a config file.
    """
    if not races:
        logger.warning("no validation races; leaving temperature at 1.0")
        return 1.0, {}

    scores = {
        candidate: evaluate_temperature(model, races, candidate, runs=runs)
        for candidate in CANDIDATES
    }
    best = min(scores, key=scores.get)
    if best in (min(CANDIDATES), max(CANDIDATES)):
        logger.warning(
            "temperature %.2f is at the edge of the sweep; the true optimum may "
            "lie outside the candidate range", best
        )
    logger.info(
        "temperature %.2f (score %.4f, vs %.4f at 1.0)",
        best, scores[best], scores.get(1.0, float("nan")),
    )
    return best, scores
