"""A network that predicts race pace, trained on lap times.

The race model learns from finishing orders: about 240 of them, one ranking
per race, each folding strategy, safety cars and retirements into the order.
Lap times give a different target: each driver's fuel- and tyre-corrected
pace in each race (``services/race_pace.py``), a continuous number averaged
over ~50 laps. From 2009 that is ~6,400 targets, and the first place in this
project a network has beaten a straight line: on 2022-23, one hidden layer of
8 tanh units explains 66% of the spread in relative pace, a linear fit 57%.

**Relative pace only.** A race's pace level depends on the circuit and the
cars of the day, so targets and predictions are both centred within each race,
and the loss is on the centred values — the network learns who is quicker than
whom, which is all a race needs.

**What it is measured against** (``scripts/evaluate_pace_network.py``): driving
the lap simulator with this predicted pace instead of the race model's
strengths, season by season, each season's network fitted only on the seasons
before it. Over 2019-2025 (145 races) the published-market score improved by
+0.012 per race, 95% CI -0.007..+0.032: better in four of seven seasons
but not clear of zero, and worst at the 2022 regulation reset, where a
network leaning on last year's pace is caught out. 2026 is a reset too. Not
used in serving; kept to be re-measured once a new rulebook has history.
"""

from typing import Optional

import numpy as np
from scipy.optimize import minimize


def centred(values: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Each value minus its group's mean: pace relative to the rest of its race."""
    out = np.array(values, dtype=float)
    for group in np.unique(groups):
        mask = groups == group
        out[mask] -= out[mask].mean()
    return out


class PaceNetwork:
    """``relative pace = w·x + v·tanh(Wx + b)``, fitted by L-BFGS on centred MSE."""

    def __init__(self, hidden: int = 8, l2: float = 10.0, l2_linear: float = 1e-3,
                 seed: int = 0) -> None:
        self.hidden = hidden
        self.l2 = l2
        self.l2_linear = l2_linear
        self.seed = seed
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None
        self.weights: Optional[np.ndarray] = None
        self.inputs = 0

    def _split(self, flat: np.ndarray):
        n, h = self.inputs, self.hidden
        linear = flat[:n]
        hidden_weights = flat[n:n + h * n].reshape(h, n)
        bias = flat[n + h * n:n + h * n + h]
        output = flat[n + h * n + h:]
        return linear, hidden_weights, bias, output

    def _forward(self, flat: np.ndarray, x: np.ndarray):
        linear, hidden_weights, bias, output = self._split(flat)
        activations = np.tanh(x @ hidden_weights.T + bias)
        return x @ linear + activations @ output, activations

    def _scale(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def fit(self, x: np.ndarray, y: np.ndarray, groups: np.ndarray) -> "PaceNetwork":
        self.mean = x.mean(axis=0)
        std = x.std(axis=0)
        # A column constant in training carries nothing learnable. Scaled by
        # infinity it reads as zero later rather than exploding the first time
        # it varies — practice pace, absent before 2018, did exactly that.
        self.std = np.where(std < 1e-9, np.inf, std)
        x = self._scale(x)
        self.inputs = x.shape[1]
        target = centred(y, groups)
        races = [np.where(groups == g)[0] for g in np.unique(groups)]
        count = float(len(target))
        n, h = self.inputs, self.hidden

        start = np.zeros(n + h * n + 2 * h)
        start[n:n + h * n] = np.random.default_rng(self.seed).normal(0.0, 0.3, h * n)

        def objective(flat):
            prediction, activations = self._forward(flat, x)
            residual = np.empty_like(prediction)
            for race in races:
                residual[race] = prediction[race] - prediction[race].mean() - target[race]
            linear, hidden_weights, _, output = self._split(flat)
            d_prediction = residual / count
            # Centring is a projection, so its gradient is centred too.
            for race in races:
                d_prediction[race] -= d_prediction[race].mean()
            gradient = np.zeros_like(flat)
            gradient[:n] = x.T @ d_prediction + self.l2_linear / count * linear
            d_pre = (d_prediction[:, None] * output[None, :]) * (1.0 - activations ** 2)
            gradient[n:n + h * n] = (d_pre.T @ x).ravel() + self.l2 / count * hidden_weights.ravel()
            gradient[n + h * n:n + h * n + h] = d_pre.sum(axis=0)
            gradient[n + h * n + h:] = activations.T @ d_prediction + self.l2 / count * output
            loss = (0.5 * float(residual @ residual) / count
                    + 0.5 * self.l2_linear / count * float(linear @ linear)
                    + 0.5 * self.l2 / count * float((hidden_weights ** 2).sum() + output @ output))
            return loss, gradient

        result = minimize(objective, start, jac=True, method="L-BFGS-B",
                          options={"maxiter": 2000})
        self.weights = result.x
        return self

    def predict(self, x: np.ndarray, groups: np.ndarray) -> np.ndarray:
        prediction, _ = self._forward(self.weights, self._scale(x))
        return centred(prediction, groups)

    def to_dict(self) -> dict:
        return {
            "hidden": self.hidden, "l2": self.l2, "l2_linear": self.l2_linear,
            "inputs": self.inputs, "mean": self.mean.tolist(),
            # Infinite spreads (constant columns) do not survive JSON; zero
            # stands in for them and is turned back on load.
            "std": [0.0 if not np.isfinite(v) else float(v) for v in self.std],
            "weights": self.weights.tolist(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "PaceNetwork":
        net = cls(hidden=raw["hidden"], l2=raw["l2"], l2_linear=raw["l2_linear"])
        net.inputs = raw["inputs"]
        net.mean = np.array(raw["mean"], dtype=float)
        std = np.array(raw["std"], dtype=float)
        net.std = np.where(std <= 0, np.inf, std)
        net.weights = np.array(raw["weights"], dtype=float)
        return net
