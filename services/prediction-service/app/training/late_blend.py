"""Late in a race, lean the live forecast toward "the order holds".

The replay backtest found one place where the live forecast loses: at 90% of
race distance "the running order is the result" beats its podium chances
(podium Brier +0.17 on the 2024-25 dry races). The simulator still spreads
chances across cars that, ten laps from the flag, almost never change places.

The fix measured here is the plainest one: past a starting share of the race,
mix the forecast with order-holds, with a weight that grows linearly to
``alpha`` at the flag::

    w(f) = alpha * clip((f - start) / (1 - start), 0, 1)
    p    = (1 - w) * forecast + w * order_holds

``alpha`` and ``start`` are chosen on the validation seasons and the result is
gated on the test seasons, per race, the way the live network is: enabled only
if winner log loss plus podium Brier improves with a 95% interval clear of
zero. Wet races are left out of both, as they are for the network.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.training import live_backtest

ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8, 0.9)
STARTS = (0.4, 0.5, 0.6, 0.7, 0.8)
LATE_BLEND_VERSION = 1


@dataclass
class Sample:
    """One race at one checkpoint: the forecast, order-holds, and the result."""

    race: str
    fraction: float
    win: np.ndarray
    podium: np.ndarray
    order_win: np.ndarray
    order_podium: np.ndarray
    expected: np.ndarray
    actual: Sequence[int]
    wet: bool = False


@dataclass
class LateBlend:
    alpha: float = 0.0
    start: float = 1.0
    enabled: bool = False
    meta: Dict = field(default_factory=dict)

    def weight(self, fraction: float) -> float:
        return weight(fraction, self.alpha, self.start)

    def apply(self, fraction, win, podium, order_win, order_podium):
        if not self.enabled:
            return win, podium
        return blend(win, podium, order_win, order_podium, self.weight(fraction))

    def to_dict(self) -> Dict:
        return {"version": LATE_BLEND_VERSION, "alpha": self.alpha, "start": self.start,
                "enabled": self.enabled, **self.meta}

    @classmethod
    def from_dict(cls, data: Dict) -> "LateBlend":
        meta = {k: v for k, v in data.items() if k not in ("alpha", "start", "enabled", "version")}
        return cls(alpha=float(data["alpha"]), start=float(data["start"]),
                   enabled=bool(data.get("enabled")), meta=meta)


def weight(fraction: float, alpha: float, start: float) -> float:
    if alpha <= 0 or start >= 1:
        return 0.0
    return float(alpha * np.clip((fraction - start) / (1.0 - start), 0.0, 1.0))


def blend(win, podium, order_win, order_podium, w: float):
    win, podium = np.asarray(win, float), np.asarray(podium, float)
    if w <= 0:
        return win, podium
    return ((1 - w) * win + w * np.asarray(order_win, float),
            (1 - w) * podium + w * np.asarray(order_podium, float))


def order_chances(running_order: Sequence[str], names: Sequence[str], retired) -> Tuple[np.ndarray, np.ndarray]:
    """Order-holds as chances: 1 for the leader's win, 1 for a top-three podium."""
    place = {d: i + 1 for i, d in enumerate(running_order)}
    retired = np.asarray(retired, bool)
    win = np.array([float(place.get(n) == 1) for n in names]) * ~retired
    podium = np.array([float(place.get(n, 999) <= live_backtest.PODIUM) for n in names]) * ~retired
    return win, podium


def _loss(sample: Sample, alpha: float, start: float) -> float:
    win, podium = blend(sample.win, sample.podium, sample.order_win, sample.order_podium,
                        weight(sample.fraction, alpha, start))
    scores = live_backtest.score_probabilities(win, podium, sample.expected, sample.actual)
    return scores["winner_log_loss"] + scores["podium_brier"]


def _usable(samples: Sequence[Sample]) -> List[Sample]:
    return [s for s in samples if s.fraction > 0 and not s.wet and 1 in list(s.actual)]


def choose(samples: Sequence[Sample], alphas=ALPHAS, starts=STARTS):
    """``(alpha, start, search)``: the pair with the lowest mean loss."""
    usable = _usable(samples)
    search = {}
    for alpha in alphas:
        for start in starts:
            search[(alpha, start)] = float(np.mean([_loss(s, alpha, start) for s in usable])) \
                if usable else float("nan")
    best = min(search, key=lambda k: (search[k], k[0]))
    return best[0], best[1], search


def gate(samples: Sequence[Sample], alpha: float, start: float, resamples=2000, seed=0):
    """Blended minus unblended, per race, over the checkpoints the blend
    touches; mean and 95% bootstrap interval. Enabled only if all below zero."""
    per_race: Dict[str, List[float]] = {}
    for s in _usable(samples):
        if weight(s.fraction, alpha, start) <= 0:
            continue
        per_race.setdefault(s.race, []).append(_loss(s, alpha, start) - _loss(s, 0.0, 1.0))
    diffs = np.array([np.mean(v) for v in per_race.values()])
    if not diffs.size:
        return float("nan"), (float("nan"), float("nan")), False
    rng = np.random.default_rng(seed)
    means = diffs[rng.integers(0, diffs.size, size=(resamples, diffs.size))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(diffs.mean()), (float(low), float(high)), bool(high < 0)


def load(path: str) -> Optional[LateBlend]:
    """The artifact at ``path``, or None when there is none or it is unreadable."""
    try:
        with open(os.path.abspath(path)) as handle:
            return LateBlend.from_dict(json.load(handle))
    except (OSError, ValueError, KeyError):
        return None
