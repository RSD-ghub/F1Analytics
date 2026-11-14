"""A deeper network for the next lap time, learned from every clean lap.

The race result gives one answer per race, about 170 since 2018, which is why
the race-level networks are kept tiny. Lap times are different: every clean lap
is its own answer, about 200,000 of them from 2014 on. That is enough data for
a network with two hidden layers, and it is where "learn every parameter over
every lap" can pay off.

**What it predicts.** A driver's next clean lap, as percent over the race's
typical clean lap. The level of a race (the circuit, the day) is the
simulator's business; what decides a race is who is quicker than whom on the
same lap, so the main score is the error after centring each lap across the
field (``centred_rmse``).

**What it reads** (``LAP_FEATURES`` plus the race model's pre-race features
for the driver, ``feature_vector``):

* where the race is (share run, which is the fuel burnt), tyre compound and
  age, which stint;
* traffic at the start of the lap: place on the road, gap to the car ahead;
* laps since the last safety car or VSC, seen in the field's lap times rather
  than read from race control, so 2014-2017 has it too;
* race weather (track and air temperature, rain), when there is any;
* the circuit's archetype, and the era;
* **what the race has shown so far**: the driver's pace against the field on
  clean laps before this one, how many there were, and how fast this stint's
  tyres are fading. Before the first clean lap these are zero, so one model
  serves both before the race and during it.

Inputs a season does not have are zero with a flag beside them: no compound
before 2018 (``compound_unknown``), no weather before 2018 (``weather_known``).

**Shape.** ``y = w·x + c + v·tanh(W2 tanh(W1 x + b1) + b2)``: a linear part
plus a stack of tanh layers, fitted by L-BFGS on weighted squared error with an
L2 penalty. ``hidden=()`` is the plain linear model on the same inputs, the
first thing it has to beat. Plain numpy with a hand-written gradient, like the
other networks here.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize

from app.services import race_pace
from app.services.ingestion_client import RaceLap

LAP_TIME_NETWORK_VERSION = "lap-net-v1"

COMPOUNDS = ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET")
WET = ("INTERMEDIATE", "WET")
ARCHETYPES = ("power", "balanced", "technical")

GAP_CAP = 5.0
SINCE_NEUTRAL_CAP = 10
#: Clean laps of a driver's own running worth as much as knowing nothing; the
#: shrinkage on the in-race pace, as ``REPLAY_PRIOR_LAPS`` is for the live sim.
LIVE_PRIOR_LAPS = 3.0
#: Shrinkage on the in-stint fade slope, in (laps of tyre age) squared.
FADE_PRIOR = 30.0

LAP_FEATURES = (
    "race_share",           # laps run / race distance: the fuel burnt
    "tyre_age",
    "stint",
    *("compound_" + c.lower() for c in COMPOUNDS),
    "compound_unknown",
    "position_share",       # place on the road at the start of the lap / field
    "gap_ahead",            # seconds to the car in front, capped
    "traffic_known",
    "since_neutralised",    # laps since a safety car / VSC lap, capped
    "track_temp",
    "air_temp",
    "rain",
    "weather_known",
    *("archetype_" + a for a in ARCHETYPES),
    "era_2017",             # wide cars from 2017
    "era_2022",             # ground effect from 2022
    "live_pace",            # pace against the field on clean laps so far, shrunk
    "live_laps",
    "stint_fade",           # percent per lap of age, this stint so far, shrunk
)
LIVE_COLUMNS = tuple(LAP_FEATURES.index(n) for n in ("live_pace", "live_laps", "stint_fade"))


def neutralised_laps(laps: Sequence[RaceLap]) -> Tuple[set, float]:
    """Laps the field ran slowly (safety car, VSC, red flag), and the race's
    typical lap: the same rule ``race_pace`` uses to drop them."""
    by_lap: Dict[int, List[float]] = {}
    for lap in laps:
        if lap.lap_time_seconds > 0 and lap.lap >= 2:
            by_lap.setdefault(lap.lap, []).append(lap.lap_time_seconds)
    if not by_lap:
        return set(), 0.0
    medians = {n: float(np.median(t)) for n, t in by_lap.items()}
    typical = float(np.median(list(medians.values())))
    return {n for n, m in medians.items() if m > race_pace.NEUTRALISED_FACTOR * typical}, typical


def _cumulative(laps: Sequence[RaceLap]) -> Dict[int, Dict[str, float]]:
    """Lap number -> driver -> race time at the end of that lap, for drivers
    whose every lap so far was timed."""
    out: Dict[int, Dict[str, float]] = {}
    by_driver: Dict[str, List[RaceLap]] = {}
    for lap in laps:
        by_driver.setdefault(lap.driver, []).append(lap)
    for driver, rows in by_driver.items():
        total, expected = 0.0, 1
        for lap in sorted(rows, key=lambda r: r.lap):
            if lap.lap != expected or lap.lap_time_seconds <= 0:
                break
            total += lap.lap_time_seconds
            out.setdefault(lap.lap, {})[driver] = total
            expected += 1
    return out


def _stint_starts(laps: Sequence[RaceLap]) -> Dict[Tuple[str, int], int]:
    first: Dict[Tuple[str, int], int] = {}
    for lap in laps:
        key = (lap.driver, lap.stint)
        first[key] = min(first.get(key, lap.lap), lap.lap)
    return first


class RaceRows:
    """One race's clean laps as rows, in lap order."""

    def __init__(self, key, drivers, lap_numbers, x, y, compounds, ages, typical):
        self.key = key                  # (season, round)
        self.drivers = drivers
        self.laps = lap_numbers
        self.x = x                      # (rows, len(LAP_FEATURES))
        self.y = y                      # percent over the typical lap
        self.compounds = compounds
        self.ages = ages
        self.typical = typical          # seconds


def race_rows(
    season: int,
    round_number: int,
    laps: Sequence[RaceLap],
    total_laps: int,
    archetype: str = "",
    weather: Optional[Mapping[str, float]] = None,
    tyre_ages: Optional[Mapping[Tuple[str, int], int]] = None,
    neutral: Optional[Iterable[int]] = None,
) -> Optional[RaceRows]:
    """Rows for every clean lap of one race, each with only what was known at
    the start of that lap. None for a race with no clean running.

    ``tyre_ages`` and ``neutral`` (safety car and VSC laps) come from an outside
    record where the stored laps lack them (``tum_timing`` for 2014-2017): the
    ages replace the ones counted from stint starts, and the laps join the ones
    seen in the field's lap times.
    """
    seen_neutral, typical = neutralised_laps(laps)
    neutral = seen_neutral | set(neutral or ())
    clean = [lap for lap in race_pace._clean_laps(laps) if lap.lap not in neutral]
    if not clean or total_laps < 2 or typical <= 0:
        return None
    tyre_ages = tyre_ages or {}
    unknown = all(lap.compound.upper() == "UNKNOWN" for lap in clean)
    # Stint starts from every lap where stints are recorded; from the clean
    # laps (stints inferred from pit spikes) where they are not.
    recorded = [lap for lap in laps if lap.stint > 0]
    starts = _stint_starts(recorded if recorded else clean)
    cumulative = _cumulative(laps)
    position_of = {(lap.driver, lap.lap): lap.position for lap in laps if lap.position > 0}
    field = max(len({lap.driver for lap in laps}), 1)
    weather = weather or {}
    archetype = (archetype or "").lower()

    by_lap: Dict[int, List[RaceLap]] = {}
    for lap in clean:
        by_lap.setdefault(lap.lap, []).append(lap)
    y_of = {(lap.driver, lap.lap): 100.0 * (lap.lap_time_seconds / typical - 1.0) for lap in clean}

    # Running per-driver evidence from the race so far, updated after each lap.
    seen_sum: Dict[str, float] = {}
    seen_count: Dict[str, int] = {}
    stint_history: Dict[Tuple[str, int], List[Tuple[float, float]]] = {}

    def age_of(lap: RaceLap) -> int:
        known = tyre_ages.get((lap.driver, lap.lap))
        if known is not None:
            return int(known)
        return lap.lap - starts.get((lap.driver, lap.stint), lap.lap) + 1

    rows_x, rows_y, drivers, numbers, compounds, ages = [], [], [], [], [], []
    last_neutral = 0
    for number in range(2, max(by_lap) + 1):
        if number - 1 in neutral:
            last_neutral = number - 1
        before = cumulative.get(number - 1, {})
        ordered = sorted(before.values())
        for lap in by_lap.get(number, []):
            d = lap.driver
            stint = lap.stint if lap.stint > 0 else 1
            compound = lap.compound.upper()
            mine = before.get(d)
            ahead_gap, traffic, place = GAP_CAP, 0.0, position_of.get((d, number - 1))
            if mine is not None:
                k = int(np.searchsorted(ordered, mine))
                if k > 0:
                    ahead_gap = min(mine - ordered[k - 1], GAP_CAP)
                traffic = 1.0
                if place is None:
                    place = k + 1
            count = seen_count.get(d, 0)
            fade = 0.0
            history = stint_history.get((d, stint), [])
            if len(history) >= 3:
                a = np.array([h[0] for h in history])
                r = np.array([h[1] for h in history])
                a = a - a.mean()
                fade = float(a @ (r - r.mean())) / (float(a @ a) + FADE_PRIOR)
            rows_x.append([
                number / total_laps,
                float(age_of(lap)),
                float(stint),
                *(1.0 if compound == c else 0.0 for c in COMPOUNDS),
                1.0 if unknown or compound not in COMPOUNDS else 0.0,
                (place or field) / field,
                ahead_gap,
                traffic,
                float(min(number - last_neutral, SINCE_NEUTRAL_CAP)) if last_neutral
                else float(SINCE_NEUTRAL_CAP),
                float(weather.get("track_temp", 0.0)),
                float(weather.get("air_temp", 0.0)),
                float(weather.get("rain", 0.0)),
                1.0 if weather else 0.0,
                *(1.0 if archetype == a else 0.0 for a in ARCHETYPES),
                1.0 if season >= 2017 else 0.0,
                1.0 if season >= 2022 else 0.0,
                seen_sum.get(d, 0.0) / (count + LIVE_PRIOR_LAPS),
                float(count),
                fade,
            ])
            rows_y.append(y_of[(d, number)])
            drivers.append(d)
            numbers.append(number)
            compounds.append(compound)
            ages.append(age_of(lap))
        # Only now does this lap become evidence for the laps after it.
        on_lap = by_lap.get(number, [])
        if on_lap:
            median = float(np.median([y_of[(lap.driver, number)] for lap in on_lap]))
            for lap in on_lap:
                relative = y_of[(lap.driver, number)] - median
                seen_sum[lap.driver] = seen_sum.get(lap.driver, 0.0) + relative
                seen_count[lap.driver] = seen_count.get(lap.driver, 0) + 1
                stint = lap.stint if lap.stint > 0 else 1
                stint_history.setdefault((lap.driver, stint), []).append(
                    (float(age_of(lap)), relative))
    if not rows_x:
        return None
    return RaceRows(
        key=(season, round_number), drivers=drivers, lap_numbers=np.array(numbers),
        x=np.array(rows_x, float), y=np.array(rows_y, float), compounds=compounds,
        ages=np.array(ages, float), typical=typical)


def race_weather(rows: Sequence[Mapping]) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Mean track and air temperature and the share of samples with rain, per
    race, from ingestion's weather rows."""
    grouped: Dict[Tuple[int, int], List[Mapping]] = {}
    for row in rows:
        grouped.setdefault((int(row["season"]), int(row["round"])), []).append(row)
    out = {}
    for key, samples in grouped.items():
        track = [float(s["track_temp"]) for s in samples if s.get("track_temp")]
        air = [float(s["air_temp"]) for s in samples if s.get("air_temp")]
        if not track and not air:
            continue
        out[key] = {
            "track_temp": float(np.mean(track)) if track else 0.0,
            "air_temp": float(np.mean(air)) if air else 0.0,
            "rain": float(np.mean([bool(s.get("rainfall")) for s in samples])),
        }
    return out


def centred(values, groups) -> np.ndarray:
    """Each value minus its group's mean. Groups are (race, lap)."""
    values = np.asarray(values, float)
    _, inverse = np.unique(np.asarray(groups), return_inverse=True)
    sums = np.bincount(inverse, weights=values)
    counts = np.bincount(inverse)
    return values - (sums / counts)[inverse]


def centred_rmse(prediction, target, groups) -> float:
    error = centred(prediction, groups) - centred(target, groups)
    return float(np.sqrt(np.mean(error ** 2)))


class LapTimeNetwork:
    """``y = w·x + c + v·tanh(...tanh(W1 x + b1)...)``; ``hidden=()`` is linear."""

    def __init__(self, hidden: Sequence[int] = (32, 16), l2: float = 1.0,
                 l2_linear: float = 1e-3, seed: int = 0, max_iter: int = 600) -> None:
        self.hidden = tuple(int(h) for h in hidden)
        self.l2 = l2
        self.l2_linear = l2_linear
        self.seed = seed
        self.max_iter = max_iter
        self.inputs = 0
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None
        self.weights: Optional[np.ndarray] = None

    # Layout: linear (n), intercept (1), then per hidden layer its weights
    # (h_i, h_{i-1}) and bias (h_i), then the output weights (h_last).
    def _shapes(self):
        shapes = [(self.inputs,), (1,)]
        previous = self.inputs
        for width in self.hidden:
            shapes += [(width, previous), (width,)]
            previous = width
        if self.hidden:
            shapes.append((previous,))
        return shapes

    def _split(self, flat):
        out, i = [], 0
        for shape in self._shapes():
            size = int(np.prod(shape))
            out.append(flat[i:i + size].reshape(shape))
            i += size
        return out

    def _forward(self, flat, x):
        parts = self._split(flat)
        activations = [x]
        for k in range(len(self.hidden)):
            w, b = parts[2 + 2 * k], parts[3 + 2 * k]
            activations.append(np.tanh(activations[-1] @ w.T + b))
        y = x @ parts[0] + parts[1][0]
        if self.hidden:
            y = y + activations[-1] @ parts[-1]
        return y, activations, parts

    def _scale(self, x):
        return (x - self.mean) / self.std

    def _objective(self, flat, x, y, share):
        """Weighted squared error plus the L2 penalty, and its gradient."""
        count = float(len(y))
        prediction, activations, parts = self._forward(flat, x)
        residual = prediction - y
        d_out = share * residual
        loss = 0.5 * float(d_out @ residual)
        loss += 0.5 * self.l2_linear / count * float(parts[0] @ parts[0])
        grads = [x.T @ d_out + self.l2_linear / count * parts[0], np.array([d_out.sum()])]
        if self.hidden:
            output = parts[-1]
            loss += 0.5 * self.l2 / count * float(output @ output)
            g_output = activations[-1].T @ d_out + self.l2 / count * output
            delta = (d_out[:, None] * output[None, :]) * (1.0 - activations[-1] ** 2)
            layers = []
            for k in reversed(range(len(self.hidden))):
                w = parts[2 + 2 * k]
                loss += 0.5 * self.l2 / count * float((w ** 2).sum())
                layers = [delta.T @ activations[k] + self.l2 / count * w,
                          delta.sum(axis=0)] + layers
                if k > 0:
                    delta = (delta @ w) * (1.0 - activations[k] ** 2)
            grads += layers + [g_output]
        return loss, np.concatenate([g.ravel() for g in grads])

    def fit(self, x, y, weights=None) -> "LapTimeNetwork":
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        self.mean = x.mean(axis=0)
        std = x.std(axis=0)
        # A column constant in training reads as zero later, not as a blow-up.
        self.std = np.where(std < 1e-9, np.inf, std)
        x = self._scale(x)
        self.inputs = x.shape[1]
        share = np.ones(len(y)) if weights is None else np.asarray(weights, float)
        share = share / share.sum()

        rng = np.random.default_rng(self.seed)
        shapes = self._shapes()
        start = []
        for i, shape in enumerate(shapes):
            if len(shape) == 2:
                start.append(rng.normal(0.0, 1.0 / np.sqrt(shape[1]), shape).ravel())
            elif self.hidden and i == len(shapes) - 1:
                start.append(rng.normal(0.0, 0.1, shape))
            else:
                start.append(np.zeros(shape))
        start = np.concatenate(start)
        start[self.inputs] = float(share @ y)  # the intercept

        def objective(flat):
            return self._objective(flat, x, y, share)

        result = minimize(objective, start, jac=True, method="L-BFGS-B",
                          options={"maxiter": self.max_iter})
        self.weights = result.x
        return self

    def predict(self, x) -> np.ndarray:
        prediction, _, _ = self._forward(self.weights, self._scale(np.asarray(x, float)))
        return prediction

    def to_dict(self) -> dict:
        return {
            "hidden": list(self.hidden), "l2": self.l2, "l2_linear": self.l2_linear,
            "inputs": self.inputs, "mean": self.mean.tolist(),
            "std": [0.0 if not np.isfinite(v) else float(v) for v in self.std],
            "weights": self.weights.tolist(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "LapTimeNetwork":
        net = cls(hidden=raw["hidden"], l2=raw["l2"], l2_linear=raw["l2_linear"])
        net.inputs = raw["inputs"]
        net.mean = np.array(raw["mean"], float)
        std = np.array(raw["std"], float)
        net.std = np.where(std <= 0, np.inf, std)
        net.weights = np.array(raw["weights"], float)
        return net


def simulator_prediction(rows: RaceRows, prior_pct: Mapping[str, float], profile: Mapping,
                         prior_laps: float = LIVE_PRIOR_LAPS) -> np.ndarray:
    """What today's simulator expects each of these laps to be, in the same
    units as ``rows.y``: the car's pre-race pace (``prior_pct``, percent of a
    lap, from the pace network as the lap engine scales it) plus the circuit
    profile's compound offset and wear for the tyre's age. During the race the
    pace is blended with the car's clean laps so far, as ``live_forecast.
    posterior_pace`` does: observed against the median car on each lap, shifted
    to the prior's level, weighted ``n / (n + prior_laps)``.

    Fuel is the same for every car on a lap, so it is left out: the scores are
    centred per lap.
    """
    tyres = profile["tyres"]
    typical = float(profile.get("typical_lap") or rows.typical)

    def tyre_pct(compound, age):
        row = tyres.get(compound) or tyres.get("MEDIUM") or {"offset": 0.0, "wear": 0.0}
        return 100.0 * (row["offset"] + row["wear"] * age) / typical

    tyre = np.array([tyre_pct(c, a) for c, a in zip(rows.compounds, rows.ages)])
    prior = np.array([prior_pct.get(d, 0.0) for d in rows.drivers])
    out = np.empty(len(rows.y))
    seen_sum: Dict[str, float] = {}
    seen_count: Dict[str, int] = {}
    for number in np.unique(rows.laps):
        idx = np.where(rows.laps == number)[0]
        drivers = [rows.drivers[i] for i in idx]
        counts = np.array([seen_count.get(d, 0) for d in drivers], float)
        observed = np.array([seen_sum.get(d, 0.0) / max(seen_count.get(d, 0), 1)
                             for d in drivers])
        seen = counts > 0
        if seen.any():
            observed[seen] += (np.average(prior[idx][seen], weights=counts[seen])
                               - np.average(observed[seen], weights=counts[seen]))
        weight = counts / (counts + prior_laps)
        pace = (1.0 - weight) * prior[idx] + weight * np.where(seen, observed, prior[idx])
        out[idx] = pace + tyre[idx]
        # This lap becomes evidence for the next ones.
        residual = rows.y[idx] - tyre[idx]
        residual = residual - np.median(residual)
        for d, r in zip(drivers, residual):
            seen_sum[d] = seen_sum.get(d, 0.0) + float(r)
            seen_count[d] = seen_count.get(d, 0) + 1
    return out


def next_lap_rows(season: int, round_number: int, laps: Sequence[RaceLap], upto: int,
                  total_laps: int, archetype: str = "",
                  weather: Optional[Mapping[str, float]] = None):
    """(driver -> (features, compound, tyre age), typical lap) for lap ``upto + 1``,
    for every car that completed lap ``upto``, from the laps up to ``upto`` only.

    Built by ``race_rows`` itself, so the features are exactly the ones the
    model was trained on: each car gets a placeholder lap ``upto + 1`` on its
    current tyre at the race's typical pace. A placeholder is never evidence,
    because a row's inputs only use the laps before it.
    """
    known = [lap for lap in laps if lap.lap <= upto]
    if upto < 1:
        return {}, 0.0
    _, typical = neutralised_laps(known)
    if typical <= 0:
        return {}, 0.0
    ghosts = [lap.model_copy(update={"lap": upto + 1, "lap_time_seconds": typical, "position": 0})
              for lap in known if lap.lap == upto]
    rows = race_rows(season, round_number, known + ghosts, total_laps, archetype, weather)
    if rows is None:
        return {}, typical
    out = {}
    for i in np.where(rows.laps == upto + 1)[0]:
        out[rows.drivers[i]] = (rows.x[i], rows.compounds[i], float(rows.ages[i]))
    return out, typical
