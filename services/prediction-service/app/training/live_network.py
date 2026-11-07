"""A small network that corrects the live simulator's chances, learned from replays.

The live simulator (``services/live_forecast.py``) restarts the lap simulator
from the race as it stands. It is physics plus measured habits, and it gets
some things systematically wrong: in the replay backtest it is too unsure of
the podium in the last laps, where "the order holds" beats it. A network can
learn corrections like that from the race state, as long as it is kept small
and has to prove itself on races it has not seen.

**Residual by construction.** For every driver at every checkpoint the network
reads the simulator's chances and the race state (``features``) and outputs
two adjustments: one to the log of the win chance, one to the log-odds of the
podium chance. Win chances are renormalised across the cars still running with
a softmax, so they still sum to one. With all weights at zero the output is
exactly the simulator's, and the L2 penalty pulls toward that: the network
moves the simulator only where the replays show a consistent error.

**Shape.** ``adjustment = w·x + v·tanh(Wx + b)``, one hidden layer shared by
the two heads, fitted by L-BFGS on the win log loss (softmax per race and
checkpoint) plus the podium log loss.

**Training data** is replays of races the simulator's strength model and
circuit profiles did not see (``scripts/backtest_live.py --network``), at every
5% of race distance. That is thousands of driver rows but only as many
independent races as the training seasons hold, about 40, so the hidden layer
is 6 units and one penalty, on every weight, is chosen by cross-validation
across seasons.
"""

from typing import Dict, List, Optional, Sequence

import numpy as np
from scipy.optimize import minimize

from app.services.live_state import RaceState

LIVE_NETWORK_VERSION = "live-net-v1"

FEATURES = (
    "distance_done",        # share of the race run
    "laps_left",
    "position_share",       # running position / field size
    "gap_to_leader",        # seconds, capped
    "gap_ahead",            # seconds to the car in front on the road, capped
    "gap_behind",           # seconds to the car behind, capped
    "tyre_age",
    "stops_made",
    "owes_compound",        # dry race, has not yet run two compounds
    "pace_this_race",       # seconds per lap against the field (posterior)
    "clean_laps",
    "neutralised",          # a safety car or VSC is out
    "sim_expected_share",   # simulator's expected place / field size
    "sim_retire",
    "orderliness",          # how rarely places change at this circuit
)
GAP_CAP = 60.0
#: The least chance fed to a log, so a car the simulator never saw win can
#: still be lifted a little.
FLOOR = 1e-3


def features(state: RaceState, pace: np.ndarray, expected: np.ndarray,
             retire: np.ndarray, profile: Dict) -> np.ndarray:
    """(n drivers, len(FEATURES)) for one race at one checkpoint."""
    n = len(state.drivers)
    order = state.running_order()
    position = np.array([order.index(d) + 1 for d in state.drivers], float)
    on_road = sorted((state.gaps[i], i) for i in range(n) if not state.retired[i])
    ahead = np.full(n, GAP_CAP)
    behind = np.full(n, GAP_CAP)
    for k, (gap, i) in enumerate(on_road):
        if k > 0:
            ahead[i] = min(gap - on_road[k - 1][0], GAP_CAP)
        if k + 1 < len(on_road):
            behind[i] = min(on_road[k + 1][0] - gap, GAP_CAP)
    owes = np.full(n, not state.wet) & (state.used.sum(axis=1) < 2)
    pace = np.asarray(pace, float)
    running = ~state.retired
    relative_pace = pace - pace[running].mean() if running.any() else pace
    neutral = float(state.safety_car_laps > 0 or state.vsc_laps > 0)
    columns = [
        np.full(n, state.lap / max(state.total_laps, 1)),
        np.full(n, float(state.total_laps - state.lap)),
        position / n,
        np.where(state.retired, 2 * GAP_CAP, np.minimum(state.gaps, 2 * GAP_CAP)),
        ahead,
        behind,
        state.tyre_age,
        state.stops.astype(float),
        owes.astype(float),
        relative_pace,
        state.observed_laps.astype(float),
        np.full(n, neutral),
        np.asarray(expected, float) / n,
        np.asarray(retire, float),
        np.full(n, float(profile.get("orderliness", 0.78))),
    ]
    return np.column_stack(columns)


class Sample:
    """One race at one checkpoint: what the network sees and what happened."""

    def __init__(self, x: np.ndarray, sim_win: np.ndarray, sim_podium: np.ndarray,
                 running: np.ndarray, winner: int, podium: np.ndarray, group=None) -> None:
        self.x = np.asarray(x, float)
        self.sim_win = np.asarray(sim_win, float)
        self.sim_podium = np.asarray(sim_podium, float)
        #: Cars still in the race: a retired car's chances stay zero.
        self.running = np.asarray(running, bool)
        #: Index of the real winner, -1 if none is in the field.
        self.winner = int(winner)
        self.podium = np.asarray(podium, float)
        #: The season, for cross-validation.
        self.group = group


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), FLOOR, 1 - FLOOR)
    return np.log(p / (1 - p))


class LiveNetwork:
    """Adjustments to the simulator's win and podium chances; see the module notes."""

    def __init__(self, hidden: int = 6, l2: float = 10.0, l2_linear: Optional[float] = None,
                 seed: int = 0) -> None:
        self.hidden = hidden
        self.l2 = l2
        # The linear part is penalised as hard as the hidden layer by default:
        # with ~40 independent races, fifteen free slopes per head overfit too.
        self.l2_linear = l2 if l2_linear is None else l2_linear
        self.seed = seed
        self.inputs = 0
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None
        self.weights: Optional[np.ndarray] = None

    def _split(self, flat: np.ndarray):
        """Linear win (k), linear podium (k), hidden weights (h, k), hidden
        bias (h), output win (h), output podium (h)."""
        k, h = self.inputs, self.hidden
        cuts = np.cumsum([k, k, h * k, h, h])
        lw, lp, w, b, vw, vp = np.split(flat, cuts)
        return lw, lp, w.reshape(h, k), b, vw, vp

    def _scale(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def _adjust(self, flat: np.ndarray, x: np.ndarray):
        lw, lp, w, b, vw, vp = self._split(flat)
        a = np.tanh(x @ w.T + b)
        return x @ lw + a @ vw, x @ lp + a @ vp, a

    def fit(self, samples: Sequence[Sample]) -> "LiveNetwork":
        x = np.vstack([s.x for s in samples])
        self.mean = x.mean(axis=0)
        std = x.std(axis=0)
        self.std = np.where(std < 1e-9, np.inf, std)
        x = self._scale(x)
        self.inputs = k = x.shape[1]
        h = self.hidden
        bounds = np.cumsum([0] + [len(s.sim_win) for s in samples])
        base_win = np.concatenate([np.log(np.maximum(s.sim_win, FLOOR)) for s in samples])
        base_pod = np.concatenate([_logit(s.sim_podium) for s in samples])
        running = np.concatenate([s.running for s in samples])
        y_pod = np.concatenate([s.podium for s in samples])
        winners = [(bounds[j], bounds[j + 1], s.winner) for j, s in enumerate(samples)
                   if s.winner >= 0 and s.running[s.winner]]
        count = float(len(samples))
        mask = running.astype(float)

        start = np.zeros(2 * k + h * k + 3 * h)
        start[2 * k:2 * k + h * k] = np.random.default_rng(self.seed).normal(0.0, 0.1, h * k)

        def objective(flat):
            dw, dp, a = self._adjust(flat, x)
            lw, lp, w, _, vw, vp = self._split(flat)
            loss = 0.0
            d_win = np.zeros_like(dw)
            score = np.where(running, base_win + dw, -np.inf)
            for lo, hi, winner in winners:
                s = score[lo:hi]
                live = np.isfinite(s)
                e = np.where(live, np.exp(np.where(live, s, 0.0) - s[live].max()), 0.0)
                p = e / e.sum()
                loss -= float(np.log(max(p[winner], 1e-12)))
                d_win[lo:hi] = p
                d_win[lo + winner] -= 1.0
            z = base_pod + dp
            q = 1.0 / (1.0 + np.exp(-z))
            loss += float((mask * (np.logaddexp(0.0, z) - y_pod * z)).sum())
            d_pod = mask * (q - y_pod)
            loss /= count
            d_win /= count
            d_pod /= count
            loss += 0.5 / count * (self.l2_linear * float(lw @ lw + lp @ lp)
                                   + self.l2 * float((w ** 2).sum() + vw @ vw + vp @ vp))
            d_pre = (d_win[:, None] * vw[None, :] + d_pod[:, None] * vp[None, :]) * (1.0 - a ** 2)
            grad = np.concatenate([
                x.T @ d_win + self.l2_linear / count * lw,
                x.T @ d_pod + self.l2_linear / count * lp,
                (d_pre.T @ x).ravel() + self.l2 / count * w.ravel(),
                d_pre.sum(axis=0),
                a.T @ d_win + self.l2 / count * vw,
                a.T @ d_pod + self.l2 / count * vp,
            ])
            return loss, grad

        result = minimize(objective, start, jac=True, method="L-BFGS-B",
                          options={"maxiter": 1000})
        self.weights = result.x
        return self

    def predict(self, x: np.ndarray, sim_win: np.ndarray, sim_podium: np.ndarray,
                running: np.ndarray):
        """(win, podium) chances for one race at one checkpoint."""
        running = np.asarray(running, bool)
        dw, dp, _ = self._adjust(self.weights, self._scale(np.asarray(x, float)))
        score = np.log(np.maximum(sim_win, FLOOR)) + dw
        win = np.zeros(len(score))
        if running.any():
            e = np.exp(score[running] - score[running].max())
            win[running] = e / e.sum()
        podium = np.where(running, 1.0 / (1.0 + np.exp(-(_logit(sim_podium) + dp))), 0.0)
        return win, podium

    def to_dict(self) -> dict:
        return {
            "hidden": self.hidden, "l2": self.l2, "l2_linear": self.l2_linear,
            "inputs": self.inputs, "features": list(FEATURES),
            "mean": self.mean.tolist(),
            "std": [0.0 if not np.isfinite(v) else float(v) for v in self.std],
            "weights": self.weights.tolist(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "LiveNetwork":
        net = cls(hidden=raw["hidden"], l2=raw["l2"], l2_linear=raw["l2_linear"])
        net.inputs = raw["inputs"]
        net.mean = np.array(raw["mean"], dtype=float)
        std = np.array(raw["std"], dtype=float)
        net.std = np.where(std <= 0, np.inf, std)
        net.weights = np.array(raw["weights"], dtype=float)
        return net


def held_out_loss(net: Optional[LiveNetwork], samples: Sequence[Sample]) -> float:
    """Mean per sample of win log loss plus podium log loss; ``net`` None is
    the simulator alone."""
    total = 0.0
    for s in samples:
        if net is None:
            win, podium = s.sim_win, np.where(s.running, s.sim_podium, 0.0)
        else:
            win, podium = net.predict(s.x, s.sim_win, s.sim_podium, s.running)
        if s.winner >= 0:
            total -= float(np.log(max(win[s.winner], FLOOR)))
        p = np.clip(podium, FLOOR, 1 - FLOOR)
        total -= float((s.running * (s.podium * np.log(p)
                                     + (1 - s.podium) * np.log(1 - p))).sum())
    return total / max(len(samples), 1)


def choose_l2(samples: List[Sample], candidates=(3.0, 10.0, 30.0, 100.0, 300.0),
              hidden: int = 6):
    """The penalty with the best held-out loss, each group (season) held out
    in turn. Returns (best, {l2: mean held-out loss}, simulator's own loss)."""
    groups = sorted({s.group for s in samples})
    scores = {}
    for l2 in candidates:
        losses = []
        for held in groups:
            train = [s for s in samples if s.group != held]
            test = [s for s in samples if s.group == held]
            if train and test:
                losses.append(held_out_loss(LiveNetwork(hidden=hidden, l2=l2).fit(train), test))
        scores[l2] = float(np.mean(losses)) if losses else float("inf")
    baseline = float(np.mean([held_out_loss(None, [s for s in samples if s.group == g])
                              for g in groups]))
    return min(scores, key=scores.get), scores, baseline
