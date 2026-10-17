"""Fitting the neural race model.

**What the network replaces, and what it does not.** The linear model scores a
driver as ``w · x``. The network scores them as

    strength = w · x  +  v · tanh(W x + b)

— the same linear term, plus one hidden layer that can learn interactions the
linear model cannot express: a grid slot being worth more to a car with strong
race pace than to one that only qualifies well, or a reliability record
mattering more from the front than from the back. Everything downstream is
unchanged. Strengths still become finishing orders through Gumbel noise and
ranking, so the network is still a Plackett-Luce model and is fitted by the
same censored rank likelihood as ``plackett_luce.py``.

**The linear term is kept on purpose.** With ``v = 0`` the network *is* the
linear model, so the hidden layer has to earn every bit of likelihood it adds.
A network without the skip connection would have to rediscover "a better grid
slot is better" through tanh units, and with a couple of hundred training races
it would do that badly.

**Small, and penalised.** A few hundred races is not much data for a network.
Hidden width and the penalty on the hidden layer are chosen on the validation
seasons, never on the test seasons, and the test score is what decides whether
the network may replace the linear model at all (see
``scripts/train_neural.py``). A network that cannot beat a straight line on
races it has never seen does not ship.

Plain numpy with a hand-written gradient rather than a deep-learning framework:
the network has a few hundred parameters, scipy's L-BFGS fits it in seconds, and
the serving image stays the size it is.
"""

import logging
from typing import Dict, List, Sequence, Tuple

import numpy as np

from app.training.plackett_luce import RaceObservation

logger = logging.getLogger(__name__)

DEFAULT_HIDDEN = 8
#: Ridge on the linear term — the same light insurance the linear fit uses.
DEFAULT_L2_LINEAR = 1e-3
#: Ridge on the hidden layer. Much heavier: this is the part that can overfit.
DEFAULT_L2_HIDDEN = 1e-1

#: Candidate (hidden width, hidden penalty) pairs, chosen on validation seasons.
SEARCH_GRID = (
    (4, 1e-1), (4, 1.0),
    (8, 1e-1), (8, 1.0),
    (16, 1e-1), (16, 1.0),
)


class NetworkParameters:
    """The network's weights, with a flat view for the optimiser."""

    __slots__ = ("linear", "hidden_weights", "hidden_bias", "output")

    def __init__(self, linear, hidden_weights, hidden_bias, output) -> None:
        self.linear = np.asarray(linear, dtype=float)
        self.hidden_weights = np.asarray(hidden_weights, dtype=float)
        self.hidden_bias = np.asarray(hidden_bias, dtype=float)
        self.output = np.asarray(output, dtype=float)

    @property
    def inputs(self) -> int:
        return self.linear.shape[0]

    @property
    def hidden(self) -> int:
        return self.output.shape[0]

    @classmethod
    def initial(cls, inputs: int, hidden: int, seed: int = 0) -> "NetworkParameters":
        """Linear part and output layer at zero, hidden weights small.

        Output at zero makes the starting network exactly the uniform model, and
        the small random hidden weights break the symmetry between units so they
        can learn different things once the output layer starts to move.
        """
        rng = np.random.default_rng(seed)
        return cls(
            linear=np.zeros(inputs),
            hidden_weights=rng.normal(0.0, 0.3, size=(hidden, inputs)),
            hidden_bias=np.zeros(hidden),
            output=np.zeros(hidden),
        )

    def flatten(self) -> np.ndarray:
        return np.concatenate([
            self.linear, self.hidden_weights.ravel(), self.hidden_bias, self.output,
        ])

    @classmethod
    def unflatten(cls, flat: np.ndarray, inputs: int, hidden: int) -> "NetworkParameters":
        cuts = np.cumsum([inputs, hidden * inputs, hidden])
        linear, hidden_weights, hidden_bias, output = np.split(flat, cuts)
        return cls(linear, hidden_weights.reshape(hidden, inputs), hidden_bias, output)

    def to_dict(self) -> Dict[str, list]:
        return {
            "linear": self.linear.tolist(),
            "hidden_weights": self.hidden_weights.tolist(),
            "hidden_bias": self.hidden_bias.tolist(),
            "output": self.output.tolist(),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, list]) -> "NetworkParameters":
        return cls(
            raw["linear"], raw["hidden_weights"], raw["hidden_bias"], raw["output"]
        )


def forward(
    params: NetworkParameters, features: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Strengths for a field, and the hidden activations the gradient needs."""
    activations = np.tanh(features @ params.hidden_weights.T + params.hidden_bias)
    return features @ params.linear + activations @ params.output, activations


def _strength_gradient(
    strengths: np.ndarray, order: Sequence[int], sample_weight: float
) -> Tuple[float, np.ndarray]:
    """Rank-likelihood NLL for one race, and its gradient in the strengths.

    The same censored likelihood as ``plackett_luce.negative_log_likelihood``:
    retirements sit in every denominator and are never ranked.
    """
    shifted = strengths - strengths.max()
    exponentials = np.exp(shifted)
    remaining = np.ones(strengths.shape[0], dtype=bool)
    total = 0.0
    gradient = np.zeros_like(strengths)
    for chosen in order:
        pool_total = exponentials[remaining].sum()
        if pool_total <= 0:
            break
        total -= sample_weight * (shifted[chosen] - np.log(pool_total))
        gradient += sample_weight * np.where(remaining, exponentials, 0.0) / pool_total
        gradient[chosen] -= sample_weight
        remaining[chosen] = False
    return total, gradient


def negative_log_likelihood(
    flat: np.ndarray,
    races: Sequence[RaceObservation],
    inputs: int,
    hidden: int,
    l2_linear: float = DEFAULT_L2_LINEAR,
    l2_hidden: float = DEFAULT_L2_HIDDEN,
    use_sample_weights: bool = True,
) -> Tuple[float, np.ndarray]:
    """NLL and gradient over the whole training set, by backpropagation."""
    params = NetworkParameters.unflatten(flat, inputs, hidden)
    total = 0.0
    grad_linear = np.zeros_like(params.linear)
    grad_hidden_weights = np.zeros_like(params.hidden_weights)
    grad_hidden_bias = np.zeros_like(params.hidden_bias)
    grad_output = np.zeros_like(params.output)

    for race in races:
        sample_weight = race.weight if use_sample_weights else 1.0
        strengths, activations = forward(params, race.features)
        race_total, d_strength = _strength_gradient(strengths, race.order, sample_weight)
        total += race_total

        grad_linear += race.features.T @ d_strength
        grad_output += activations.T @ d_strength
        d_pre = (d_strength[:, None] * params.output[None, :]) * (1.0 - activations ** 2)
        grad_hidden_weights += d_pre.T @ race.features
        grad_hidden_bias += d_pre.sum(axis=0)

    total += 0.5 * l2_linear * float(params.linear @ params.linear)
    grad_linear += l2_linear * params.linear
    total += 0.5 * l2_hidden * (
        float((params.hidden_weights ** 2).sum()) + float(params.output @ params.output)
    )
    grad_hidden_weights += l2_hidden * params.hidden_weights
    grad_output += l2_hidden * params.output

    gradient = NetworkParameters(
        grad_linear, grad_hidden_weights, grad_hidden_bias, grad_output
    ).flatten()
    return total, gradient


def fit(
    races: Sequence[RaceObservation],
    hidden: int = DEFAULT_HIDDEN,
    l2_linear: float = DEFAULT_L2_LINEAR,
    l2_hidden: float = DEFAULT_L2_HIDDEN,
    seed: int = 0,
    max_iterations: int = 1000,
) -> NetworkParameters:
    """Fit by L-BFGS-B from a fixed seed, so a refit is reproducible.

    Unlike the linear fit this objective is not convex, so the starting point
    matters. The seed is fixed rather than searched over: trying many seeds and
    keeping the best on validation is one more way of fitting the validation set.
    """
    from scipy.optimize import minimize

    if not races:
        raise ValueError("no races to train on")
    inputs = races[0].features.shape[1]
    start = NetworkParameters.initial(inputs, hidden, seed=seed).flatten()

    result = minimize(
        fun=lambda flat: negative_log_likelihood(
            flat, races, inputs, hidden, l2_linear, l2_hidden
        ),
        x0=start,
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iterations},
    )
    if not result.success:
        logger.warning("optimiser did not converge cleanly: %s", result.message)
    logger.info(
        "fitted network (%s inputs, %s hidden) on %s races: NLL %.2f",
        inputs, hidden, len(races), result.fun,
    )
    return NetworkParameters.unflatten(result.x, inputs, hidden)


def mean_log_likelihood(
    params: NetworkParameters, races: Sequence[RaceObservation]
) -> float:
    """Average log-likelihood per race, unweighted and unpenalised.

    Directly comparable with ``plackett_luce.mean_log_likelihood`` on the same
    races — the head-to-head the network has to win.
    """
    if not races:
        return 0.0
    total, _ = negative_log_likelihood(
        params.flatten(), races, params.inputs, params.hidden,
        l2_linear=0.0, l2_hidden=0.0, use_sample_weights=False,
    )
    return -total / len(races)


def select(
    train: Sequence[RaceObservation],
    validation: Sequence[RaceObservation],
    grid: Sequence[Tuple[int, float]] = SEARCH_GRID,
    seed: int = 0,
) -> Tuple[NetworkParameters, Dict[str, float], List[Dict[str, float]]]:
    """Choose width and penalty on validation, and return that fit.

    Returns the chosen network, its settings, and every candidate's validation
    score, so the choice is on record rather than an unexplained constant.
    """
    sweep: List[Dict[str, float]] = []
    best = None
    for hidden, l2_hidden in grid:
        params = fit(train, hidden=hidden, l2_hidden=l2_hidden, seed=seed)
        score = mean_log_likelihood(params, validation)
        sweep.append({"hidden": hidden, "l2_hidden": l2_hidden, "validation_ll": score})
        if best is None or score > best[0]:
            best = (score, params, {"hidden": hidden, "l2_hidden": l2_hidden})
    _, params, chosen = best
    return params, chosen, sweep
