"""Scoring live forecasts by replaying finished races a lap at a time.

A replay feeds a past race to the live forecast as the live feed would: at
each checkpoint lap, ``live_state.race_state`` is built from the rows up to
that lap only, and the forecast made from it is scored against how the race
actually finished. Checkpoints are fractions of race distance, so a 44-lap Spa
and a 78-lap Monaco are compared at the same stage.

**Measures** (lower is better unless marked):

* ``winner_log_loss`` — minus the log of the chance given to the real winner,
  floored at ``PROBABILITY_FLOOR`` so one confident miss cannot dominate.
* ``winner_hit`` — share of races where the favourite won (higher is better).
* ``podium_brier`` — Brier score of every car's podium chance, summed over the
  field, averaged over races.
* ``position_error`` — mean absolute gap between each classified car's expected
  and real finishing place.

**Baselines.** The live forecast has to beat both:

* ``pre_race`` — the forecast from the grid, never updated: what seeing the race adds.
* ``order_holds`` — the running order at the checkpoint is the result, with
  certainty (floored like the others). Very hard to beat late in a race.
"""

from typing import Dict, List, Sequence, Tuple

import numpy as np

from app.services.ingestion_client import RaceLap
from app.services.live_state import RaceState

#: Fractions of race distance at which forecasts are scored. 0 is the start.
CHECKPOINTS = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9)
#: The least chance any car is given when scoring a log loss.
PROBABILITY_FLOOR = 0.01
PODIUM = 3

MEASURES = ("winner_log_loss", "winner_hit", "podium_brier", "position_error")


def checkpoint_laps(total_laps: int, fractions: Sequence[float] = CHECKPOINTS) -> List[int]:
    """The leader's lap count at each checkpoint (0 = before the start)."""
    return [min(int(round(f * total_laps)), total_laps - 1) for f in fractions]


def order_holds(state: RaceState) -> Tuple[np.ndarray, np.ndarray]:
    """The running order taken as the result: one run, (positions, retired)."""
    order = state.running_order()
    place = {d: i + 1 for i, d in enumerate(order)}
    positions = np.array([[place[d] for d in state.drivers]])
    return positions, state.retired[None, :].copy()


def score(positions: np.ndarray, retired: np.ndarray, actual: Sequence[int]) -> Dict[str, float]:
    """One forecast against one race. ``actual`` = classified place, 999 if not."""
    actual = np.asarray(actual)
    n = len(actual)
    finished = (actual >= 1) & (actual <= n)
    win = ((positions == 1) & ~retired).mean(axis=0)
    podium = ((positions <= PODIUM) & ~retired).mean(axis=0)
    expected = positions.mean(axis=0)
    winner = np.flatnonzero(actual == 1)
    out = {
        "podium_brier": float(((podium - ((actual <= PODIUM) & finished)) ** 2).sum()),
        "position_error": float(np.abs(expected[finished] - actual[finished]).mean())
        if finished.any() else float("nan"),
    }
    if winner.size:
        chance = max(float(win[winner[0]]), PROBABILITY_FLOOR)
        out["winner_log_loss"] = -float(np.log(chance))
        out["winner_hit"] = float(int(np.argmax(win)) == int(winner[0]))
    else:
        out["winner_log_loss"] = float("nan")
        out["winner_hit"] = float("nan")
    return out


def drop_laps(laps: Sequence[RaceLap], rate: float, rng: np.random.Generator) -> List[RaceLap]:
    """The race with a share of its lap times blanked, as a flaky feed would
    lose them; for measuring how much missing laps cost."""
    if rate <= 0:
        return list(laps)
    keep = rng.random(len(laps)) >= rate
    return [lap if k else lap.model_copy(update={"lap_time_seconds": 0.0})
            for lap, k in zip(laps, keep)]


def summarise(rows: Sequence[Dict]) -> Dict[Tuple[str, float], Dict[str, float]]:
    """Mean of each measure per (method, checkpoint), over races.

    ``rows`` are dicts with ``race``, ``method``, ``checkpoint`` and the measures."""
    groups: Dict[Tuple[str, float], List[Dict]] = {}
    for row in rows:
        groups.setdefault((row["method"], row["checkpoint"]), []).append(row)
    out = {}
    for key, members in sorted(groups.items()):
        out[key] = {"races": len(members)}
        for measure in MEASURES:
            values = [m[measure] for m in members if not np.isnan(m[measure])]
            out[key][measure] = float(np.mean(values)) if values else float("nan")
    return out


def paired_difference(rows: Sequence[Dict], method: str, baseline: str, checkpoint: float,
                      measure: str, resamples: int = 2000, seed: int = 0):
    """Mean of (method - baseline) over races at one checkpoint, with a 95%
    bootstrap interval over races. Negative = the method is better for every
    measure except ``winner_hit``. Returns (mean, (low, high), races)."""
    by_race: Dict[Tuple, Dict[str, float]] = {}
    for row in rows:
        if row["checkpoint"] == checkpoint and row["method"] in (method, baseline):
            by_race.setdefault(row["race"], {})[row["method"]] = row[measure]
    diffs = np.array([v[method] - v[baseline] for v in by_race.values()
                      if method in v and baseline in v
                      and not np.isnan(v[method]) and not np.isnan(v[baseline])])
    if not diffs.size:
        return float("nan"), (float("nan"), float("nan")), 0
    rng = np.random.default_rng(seed)
    means = diffs[rng.integers(0, diffs.size, size=(resamples, diffs.size))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(diffs.mean()), (float(low), float(high)), int(diffs.size)
