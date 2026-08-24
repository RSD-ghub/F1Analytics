"""Maximum-likelihood fitting of the Plackett-Luce race model.

**What is actually being fitted.** The serving model scores each driver with
``strength = w · features`` and samples finishing orders by adding Gumbel noise
and ranking. That sampling procedure *is* a Plackett-Luce distribution, which
means the weights ``w`` can be fitted directly by maximum likelihood on observed
finishing orders — no simulation needed during training.

The likelihood is the rank-ordered ("exploded") logit. A finishing order is read
as a sequence of choices: the winner is chosen from the whole field, second from
everyone remaining, and so on:

    P(order) = Π_j  exp(s_πj) / Σ_{m≥j} exp(s_πm)

**Censoring is handled properly.** Retirements are not ranked among themselves —
we know only that they finished behind every classified driver, not in what
order. So the product runs over classified finishers only, while retirements stay
in each denominator. That is exactly the partial-ranking likelihood, and it uses
the information a retirement carries without inventing an order it does not have.

**On the temperature.** Under this likelihood the scale of ``w`` *is* the
confidence: doubling the weights makes the model twice as decisive. It is
tempting to conclude that MLE therefore calibrates the model automatically and
no separate temperature is needed — that was the original claim here, and
measurement disproved it.

The reason is that this likelihood scores *whole finishing orders*, most of whose
information sits in a midfield nobody publishes probabilities about, while the
product's claims are all about the top few positions. Optimising the former
leaves the latter systematically under-confident: a post-hoc temperature fitted
on validation seasons improved mean Brier across the published markets from 0.57
to 0.43. So ``app/training/calibrate.py`` fits one, per lock window, and the
sampler runs at that value rather than at 1.0.
"""

import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Ridge penalty. With a handful of features and thousands of races the fit is
#: well determined, so this is cheap insurance against a rarely-varying feature
#: acquiring a huge weight rather than a load-bearing choice.
DEFAULT_L2 = 1e-3


class RaceObservation:
    """One race: a feature matrix and the observed finishing order.

    ``order`` holds row indices of the classified finishers, best first. Anyone
    absent from it retired, and contributes to the denominators without ever
    being ranked.
    """

    __slots__ = ("features", "order", "season", "round", "weight")

    def __init__(
        self,
        features: np.ndarray,
        order: Sequence[int],
        season: int = 0,
        round_number: int = 0,
        weight: float = 1.0,
    ) -> None:
        self.features = features
        self.order = list(order)
        self.season = season
        self.round = round_number
        # Era relevance (see training/eras.py). Scales this race's contribution
        # to the likelihood, so old rules inform the fit without dominating it.
        self.weight = weight

    @property
    def field_size(self) -> int:
        return self.features.shape[0]


def negative_log_likelihood(
    weights: np.ndarray,
    races: Sequence[RaceObservation],
    l2: float = DEFAULT_L2,
    use_sample_weights: bool = True,
) -> Tuple[float, np.ndarray]:
    """NLL and its gradient for the whole training set.

    Computed together because the optimiser needs both at every step and they
    share almost all the work.

    ``use_sample_weights`` is off during evaluation: era weighting is a fitting
    decision, and applying it to a held-out score would let the model look
    better simply by down-weighting the races it predicts worst.
    """
    total = 0.0
    gradient = np.zeros_like(weights)

    for race in races:
        sample_weight = race.weight if use_sample_weights else 1.0
        strengths = race.features @ weights
        # Subtracting the max keeps exp() in range. Plackett-Luce is invariant
        # to a constant shift in strengths, so this changes nothing else.
        shifted = strengths - strengths.max()
        exponentials = np.exp(shifted)

        remaining = np.ones(race.field_size, dtype=bool)
        for chosen in race.order:
            pool_total = exponentials[remaining].sum()
            if pool_total <= 0:
                break

            total -= sample_weight * (shifted[chosen] - np.log(pool_total))

            # d/dw of -[s_chosen - log Σ] = -(x_chosen - E[x under the pool])
            probabilities = np.where(remaining, exponentials, 0.0) / pool_total
            expected_features = probabilities @ race.features
            gradient -= sample_weight * (race.features[chosen] - expected_features)

            remaining[chosen] = False

    total += 0.5 * l2 * float(weights @ weights)
    gradient += l2 * weights
    return total, gradient


def fit(
    races: Sequence[RaceObservation],
    feature_names: Sequence[str],
    l2: float = DEFAULT_L2,
    max_iterations: int = 500,
) -> Dict[str, float]:
    """Fit weights by L-BFGS-B. Returns a name → weight mapping.

    The objective is convex in ``w`` (before regularisation it is a sum of
    log-sum-exp terms minus a linear term), so the optimum found is global and
    the starting point does not matter.
    """
    from scipy.optimize import minimize

    if not races:
        raise ValueError("no races to train on")

    dimensions = races[0].features.shape[1]
    if dimensions != len(feature_names):
        raise ValueError(
            "feature matrix has {} columns but {} names".format(
                dimensions, len(feature_names)
            )
        )

    result = minimize(
        fun=lambda w: negative_log_likelihood(w, races, l2),
        x0=np.zeros(dimensions),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iterations},
    )
    if not result.success:
        logger.warning("optimiser did not converge cleanly: %s", result.message)

    logger.info(
        "fitted %s features on %s races (NLL %.2f -> %.2f)",
        dimensions,
        len(races),
        negative_log_likelihood(np.zeros(dimensions), races, l2)[0],
        result.fun,
    )
    return {name: float(value) for name, value in zip(feature_names, result.x)}


def mean_log_likelihood(
    weights: np.ndarray, races: Sequence[RaceObservation]
) -> float:
    """Average log-likelihood per race — the held-out evaluation metric.

    Higher (less negative) is better. Reported per race so training and test
    sets of different sizes are directly comparable.
    """
    if not races:
        return 0.0
    total, _ = negative_log_likelihood(
        weights, races, l2=0.0, use_sample_weights=False
    )
    return -total / len(races)


def uniform_baseline_log_likelihood(races: Sequence[RaceObservation]) -> float:
    """Log-likelihood of the same orders under an all-weights-zero model.

    Every driver equally likely at every step. This is the bar: a fitted model
    that cannot beat it has learned nothing from the features.
    """
    if not races:
        return 0.0
    total = 0.0
    for race in races:
        pool = race.field_size
        for _ in race.order:
            total -= np.log(pool)
            pool -= 1
    return total / len(races)
