"""The race, simulated a lap at a time.

The Plackett-Luce sampler draws a finishing order in one step: strengths plus
noise, sorted. That gets each driver's chances right on average, but nothing in
it is a race. This module runs the race: thousands of times, every car, every
lap, so the things that decide real Grands Prix happen in the simulation too —
a safety car bunching the field and handing someone a cheap stop, a car stuck
behind a slower one at a circuit where passing is hard, a pit stop costing a
place, a first-lap shuffle, a retirement on lap 31.

**Where each piece comes from.** Nothing here is a hand-picked constant about a
circuit; it is all measured (``app/training/lap_profiles.py``):

* **Pace** — each car's strength from the race model, converted to seconds per
  lap by a rate fitted on how strength predicted real race pace, plus a
  race-day form offset per car whose spread was fitted the same way.
* **A lap** — the circuit's typical lap, plus fuel burn, the compound's offset
  and wear for the tyre's age, plus lap-to-lap noise of the size measured there.
* **Strategy** — a real strategy run at this circuit, drawn per car per race:
  its compound sequence and where in the race it stopped.
* **Pit stop** — the circuit's measured pit loss; about half under a safety car
  or VSC, and a car within a few laps of its stop takes the cheap one.
* **Safety car, VSC, red flag** — at the circuit's measured rates. A safety car
  closes the field up behind the leader; a VSC slows everyone equally; a red
  flag closes the field up and gives everyone a free tyre change.
* **Overtaking** — a car can only get past the one ahead with a lap-time
  advantage over the circuit's passing threshold; otherwise it is held behind.
  The threshold is scaled by how orderly races at the circuit have been.
* **Start** — the grid order plus a first-lap shuffle fitted to how much places
  change on lap 1.
* **Retirements** — each car's historical rate, clustered by race and by team
  as in ``app/training/retirements.py``, at a lap drawn across the race.

It returns finishing positions and retirements in exactly the shape
``RaceModel.sample_outcomes`` does, so the summaries built on top cannot tell
which engine produced them.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from app.models.schemas import DriverFeatures
from app.services import circuits
from app.services.model import (
    LAP_SIMULATION_PATH,
    PACE_NETWORK_PATH,
    feature_vector,
    MAX_CLUSTERED_RETIREMENT,
    ModelWeightsMissing,
    race_distance_dnf,
)

logger = logging.getLogger(__name__)

#: Bump on any change that alters output for the same input.
LAP_ENGINE_VERSION = "lap-sim-v1"

DRY = ("SOFT", "MEDIUM", "HARD")

#: Seconds between cars on the road after lap one, before the shuffle.
START_SPACING = 0.6
#: Laps a safety car stays out, and a VSC.
SAFETY_CAR_LAPS = 4
VSC_LAPS = 2
#: Gap each car keeps to the one ahead when the field is closed up.
BUNCHED_GAP = 0.5
#: Closest a car can run behind another without passing.
FOLLOW_GAP = 0.3
#: A stop planned this many laps ahead is taken early under a neutralisation.
CHEAP_STOP_WINDOW = 8
#: Pit loss under a safety car or VSC, as a fraction of the green-flag loss.
NEUTRALISED_PIT_FRACTION = 0.5
#: Share of retirements that happen on the first lap.
FIRST_LAP_RETIREMENT_SHARE = 0.2


class LapSimulationParameters:
    """The fitted constants that are not about any one circuit."""

    def __init__(
        self,
        seconds_per_strength: float,
        race_day_sd: float,
        start_shuffle_sd: float,
        overtake_threshold: float,
        orderliness_slope: float = 0.0,
        overall_orderliness: float = 0.78,
    ) -> None:
        self.seconds_per_strength = seconds_per_strength
        self.race_day_sd = race_day_sd
        self.start_shuffle_sd = start_shuffle_sd
        self.overtake_threshold = overtake_threshold
        self.orderliness_slope = orderliness_slope
        self.overall_orderliness = overall_orderliness

    def threshold_for(self, profile: Dict) -> float:
        """Harder to pass where races have stayed in grid order."""
        excess = profile.get("orderliness", self.overall_orderliness) - self.overall_orderliness
        return self.overtake_threshold * float(np.exp(self.orderliness_slope * excess))

    def to_dict(self) -> Dict[str, float]:
        return dict(
            seconds_per_strength=self.seconds_per_strength,
            race_day_sd=self.race_day_sd,
            start_shuffle_sd=self.start_shuffle_sd,
            overtake_threshold=self.overtake_threshold,
            orderliness_slope=self.orderliness_slope,
            overall_orderliness=self.overall_orderliness,
        )

    @classmethod
    def from_dict(cls, raw: Dict[str, float]) -> "LapSimulationParameters":
        return cls(**raw)


#: Used when a profile has no measured strategies (built only from seasons
#: before the compounds took their names): a one-stop, medium to hard.
FALLBACK_STRATEGIES = [{"compounds": ["MEDIUM", "HARD"], "fractions": [0.45, 0.55]}]


def _strategies(profile: Dict, rng: np.random.Generator, runs: int, n: int, laps: int):
    """Per run and car: compound per stint, and the lap each stint ends on."""
    pool = profile.get("strategies") or FALLBACK_STRATEGIES
    picks = rng.integers(0, len(pool), size=(runs, n))
    max_stints = max(len(s["compounds"]) for s in pool)
    compounds = np.full((runs, n, max_stints), -1, dtype=int)
    ends = np.full((runs, n, max_stints), laps + 1, dtype=int)
    # Built per strategy rather than per car: a few hundred strategies, not
    # two hundred thousand cars.
    for index, strategy in enumerate(pool):
        mask = picks == index
        if not mask.any():
            continue
        stints = len(strategy["compounds"])
        boundaries = np.cumsum(strategy["fractions"])[:-1]
        jitter = rng.normal(0.0, 2.0, size=(int(mask.sum()), max(stints - 1, 1)))
        for s, compound in enumerate(strategy["compounds"]):
            compounds[mask, s] = DRY.index(compound)
            if s < stints - 1:
                lap = np.clip(np.round(boundaries[s] * laps + jitter[:, s]), 2, laps - 1)
                ends[mask, s] = lap.astype(int)
        ends[mask, stints - 1] = laps + 1
    # Stops in order, even after jitter.
    ends = np.sort(ends, axis=2)
    return compounds, ends


class LiveStart:
    """Where a real race stands at the end of a lap, for ``simulate`` to run on from.

    Arrays are per driver, in the same order as the ``drivers`` passed to
    ``simulate``. Built from timing data by ``services/live_state.py``.
    """

    def __init__(
        self,
        lap: int,
        total_laps: int,
        gaps: np.ndarray,
        retired: np.ndarray,
        retired_lap: np.ndarray,
        compound: np.ndarray,
        tyre_age: np.ndarray,
        stops: np.ndarray,
        used: np.ndarray,
        pace: np.ndarray,
        pace_sd: np.ndarray,
        safety_car_laps: int = 0,
        vsc_laps: int = 0,
        penalty: Optional[np.ndarray] = None,
        wet: bool = False,
    ) -> None:
        n = len(gaps)
        #: Laps the leader has completed.
        self.lap = int(lap)
        self.total_laps = int(total_laps)
        #: Seconds behind the leader on the road (a lapped car is over a lap).
        self.gaps = np.asarray(gaps, float)
        self.retired = np.asarray(retired, bool)
        #: The lap each retired car stopped on; ignored for running cars.
        self.retired_lap = np.asarray(retired_lap, int)
        #: Index into ``DRY``; anything else (wet tyres, unknown) reads as -1.
        self.compound = np.asarray(compound, int)
        self.tyre_age = np.asarray(tyre_age, float)
        self.stops = np.asarray(stops, int)
        #: (n, 3): which dry compounds each car has raced on so far.
        self.used = np.asarray(used, bool).reshape(n, len(DRY))
        #: Seconds per lap relative to the field, already updated from the
        #: laps run so far (``live_forecast.posterior_pace``).
        self.pace = np.asarray(pace, float)
        #: What is still unknown about each car's pace, seconds per lap.
        self.pace_sd = np.asarray(pace_sd, float)
        self.safety_car_laps = int(safety_car_laps)
        self.vsc_laps = int(vsc_laps)
        #: Time penalties still to be applied at the flag, seconds.
        self.penalty = np.zeros(n) if penalty is None else np.asarray(penalty, float)
        #: A wet race: no two-compound rule, and tyres the simulator only knows
        #: as the medium.
        self.wet = bool(wet)


#: An overdue stop (the drawn plan had it on an earlier lap) is taken within
#: this many laps.
OVERDUE_STOP_WINDOW = 4


def _remaining_strategies(profile: Dict, rng: np.random.Generator, runs: int,
                          start: LiveStart):
    """Per run and car, the rest of the race's tyre plan from ``start.lap``.

    Stint 0 is the tyre each car is on now. The rest is a real strategy run at
    this circuit with at least as many stints as the car has already started,
    picked up from the stint the car is in, so a car that has stopped once is
    drawn from the one- and two-stop plans, not the no-stops. In a dry race a
    car that has not yet used two compounds, and whose plan would not, gets one
    more stop onto a different compound, as the rules require.
    """
    pool = profile.get("strategies") or FALLBACK_STRATEGIES
    n = len(start.gaps)
    laps, lap = start.total_laps, start.lap
    width = max(len(s["compounds"]) for s in pool) + 1
    compounds = np.full((runs, n, width), -1, dtype=int)
    ends = np.full((runs, n, width), laps + 1, dtype=int)
    for i in range(n):
        current = start.compound[i] if start.compound[i] >= 0 else DRY.index("MEDIUM")
        compounds[:, i, 0] = current
        if start.retired[i] or lap >= laps - 1:
            continue
        done = int(start.stops[i])
        options = [s for s in pool if len(s["compounds"]) > done]
        if not options:
            continue  # stopped more than any plan here: runs to the flag
        picks = rng.integers(0, len(options), size=runs)
        for index, strategy in enumerate(options):
            mask = picks == index
            count = int(mask.sum())
            if not count:
                continue
            planned = np.cumsum(strategy["fractions"])[:-1] * laps
            remaining = planned[done:]
            rest = [DRY.index(c) for c in strategy["compounds"][done + 1:]]
            for s, compound in enumerate(rest, start=1):
                compounds[mask, i, s] = compound
            floor = np.full(count, lap)
            for s, boundary in enumerate(remaining):
                drawn = np.round(boundary + rng.normal(0.0, 2.0, size=count))
                overdue = lap + rng.integers(1, OVERDUE_STOP_WINDOW + 1, size=count)
                end = np.where(drawn <= floor, np.maximum(overdue, floor + 1), drawn)
                end = np.minimum(end, laps - 1).astype(int)
                ends[mask, i, s] = end
                floor = end
    if not start.wet:
        _two_compound_rule(compounds, ends, rng, start)
    # Stops in order. Unused slots keep compound -1 and end past the flag.
    order = np.argsort(ends, axis=2, kind="stable")
    return (np.take_along_axis(compounds, order, axis=2),
            np.take_along_axis(ends, order, axis=2))


def _two_compound_rule(compounds: np.ndarray, ends: np.ndarray,
                       rng: np.random.Generator, start: LiveStart) -> None:
    """A dry race must use two compounds: add a stop where a plan would not."""
    laps, lap = start.total_laps, start.lap
    runs, n, width = compounds.shape
    for i in range(n):
        if start.retired[i] or lap >= laps - 2:
            continue
        planned = compounds[:, i, :]
        has = start.used[i][None, :].repeat(runs, axis=0)
        for c in range(len(DRY)):
            has[:, c] |= (planned == c).any(axis=1)
        short = has.sum(axis=1) < 2
        if not short.any():
            continue
        last = (planned >= 0).sum(axis=1) - 1
        rows = np.flatnonzero(short & (last < width - 1))
        if not rows.size:
            continue
        stint = last[rows]
        previous = np.where(stint > 0, ends[rows, i, np.maximum(stint - 1, 0)], lap)
        low = np.maximum(previous, lap) + 1
        stop = rng.integers(low, np.maximum(low + 1, laps - 1))
        current = compounds[rows, i, stint]
        other = np.where(current == DRY.index("HARD"), DRY.index("MEDIUM"), DRY.index("HARD"))
        ends[rows, i, stint] = np.minimum(stop, laps - 1)
        compounds[rows, i, stint + 1] = other
        ends[rows, i, stint + 1] = laps + 1


def simulate(
    drivers: Sequence[DriverFeatures],
    strengths: np.ndarray,
    profile: Dict,
    params: LapSimulationParameters,
    seed: int,
    runs: int,
    retirement_race_k: Optional[float] = None,
    retirement_team_k: Optional[float] = None,
    distance_fraction: float = 1.0,
    start: Optional[LiveStart] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """``runs`` races. Returns (positions, retired), each (runs, n_drivers).

    With ``start``, the races run on from a real race's state at the end of
    ``start.lap`` instead of from the grid: see ``LiveStart``.
    """
    rng = np.random.default_rng(seed)
    n = len(drivers)
    if start is not None:
        laps = max(start.total_laps, start.lap + 1)
    else:
        laps = max(int(round(profile["laps"] * distance_fraction)), 3)
    rows = np.arange(runs)[:, None]

    if start is not None:
        # Pace already updated from the laps run so far; what is left of the
        # uncertainty is per car.
        pace = start.pace[None, :] + rng.normal(0.0, 1.0, size=(runs, n)) * start.pace_sd[None, :]
    else:
        # Pace, seconds per lap relative to the field average, plus race-day form.
        relative = -(strengths - strengths.mean()) * params.seconds_per_strength
        pace = relative[None, :] + rng.normal(0.0, params.race_day_sd, size=(runs, n))

    tyres = profile["tyres"]
    offset = np.array([tyres[c]["offset"] for c in DRY])
    wear = np.array([tyres[c]["wear"] for c in DRY])
    if start is not None:
        compounds, stint_ends = _remaining_strategies(profile, rng, runs, start)
    else:
        compounds, stint_ends = _strategies(profile, rng, runs, n, laps)
    stint = np.zeros((runs, n), dtype=int)
    age = np.zeros((runs, n))
    pit_loss = profile["pit_loss"]
    lap_noise = profile["lap_noise"]
    typical = profile["typical_lap"]
    threshold = params.threshold_for(profile)

    # Retirements: which cars, and on which lap.
    p = np.array([race_distance_dnf(min(max(d.dnf_rate, 0.0), 0.9), distance_fraction)
                  for d in drivers])
    hazard = np.ones((runs, n))
    if retirement_race_k:
        hazard *= rng.gamma(retirement_race_k, 1.0 / retirement_race_k, size=runs)[:, None]
    if retirement_team_k:
        teams = sorted({d.team for d in drivers})
        team_of = np.array([teams.index(d.team) for d in drivers])
        hazard *= rng.gamma(retirement_team_k, 1.0 / retirement_team_k,
                            size=(runs, len(teams)))[:, team_of]
    if start is not None:
        # Only the distance still to run can break a car.
        left = (laps - start.lap) / laps
        p = np.array([race_distance_dnf(min(max(d.dnf_rate, 0.0), 0.9), left)
                      for d in drivers])
    retires = rng.random((runs, n)) < np.minimum(p[None, :] * hazard, MAX_CLUSTERED_RETIREMENT)
    if start is not None:
        retire_lap = rng.integers(start.lap + 1, laps + 1, size=(runs, n))
        retire_lap = np.where(retires, retire_lap, laps + 1)
        retire_lap = np.where(start.retired[None, :], start.retired_lap[None, :], retire_lap)
    else:
        first_lap = rng.random((runs, n)) < FIRST_LAP_RETIREMENT_SHARE
        retire_lap = np.where(first_lap, 1, rng.integers(2, laps + 1, size=(runs, n)))
        retire_lap = np.where(retires, retire_lap, laps + 1)

    # Neutralisations: per-lap hazards from the circuit's per-race rates.
    sc_hazard = profile["safety_cars_per_race"] / laps
    vsc_hazard = profile["vscs_per_race"] / laps
    red_hazard = profile["red_flags_per_race"] / laps
    sc_left = np.zeros(runs, dtype=int)
    vsc_left = np.zeros(runs, dtype=int)

    if start is not None:
        # The race as it stands: gaps on the road, tyres, any neutralisation.
        out = np.repeat(start.retired[None, :], runs, axis=0)
        cumulative = np.where(out, np.inf, np.repeat(start.gaps[None, :], runs, axis=0))
        age = age + start.tyre_age[None, :]
        sc_left[:] = start.safety_car_laps
        vsc_left[:] = start.vsc_laps
        first = start.lap + 1
    else:
        # Lap one: grid order, spaced out, then shuffled.
        grid = np.array([d.grid_position if d.grid_position > 0 else n for d in drivers], float)
        slot = np.argsort(np.argsort(grid, kind="stable"), kind="stable").astype(float)
        cumulative = (typical * 1.1 + slot[None, :] * START_SPACING + pace
                      + rng.normal(0.0, params.start_shuffle_sd, size=(runs, n)))
        age += 1.0
        out = retire_lap <= 1
        cumulative = np.where(out, np.inf, cumulative)
        first = 2

    for lap in range(first, laps + 1):
        # Neutralisation state for this lap.
        new_sc = (sc_left == 0) & (vsc_left == 0) & (rng.random(runs) < sc_hazard)
        new_red = (sc_left == 0) & (rng.random(runs) < red_hazard)
        new_vsc = (sc_left == 0) & (vsc_left == 0) & ~new_sc & (rng.random(runs) < vsc_hazard)
        sc_left = np.where(new_sc | new_red, SAFETY_CAR_LAPS, sc_left)
        vsc_left = np.where(new_vsc, VSC_LAPS, vsc_left)
        under_sc = sc_left > 0
        under_vsc = vsc_left > 0
        neutral = under_sc | under_vsc

        # Pit stops: planned ones, and early ones taken under a neutralisation.
        current_end = np.take_along_axis(stint_ends, stint[:, :, None], axis=2)[:, :, 0]
        planned = current_end == lap
        early = neutral[:, None] & (current_end - lap <= CHEAP_STOP_WINDOW) & (current_end <= laps)
        stopping = (planned | early) & ~out
        loss = np.where(neutral[:, None], NEUTRALISED_PIT_FRACTION * pit_loss, pit_loss)
        # A red flag is a free tyre change for everyone still running.
        stopping |= new_red[:, None] & ~out & (current_end <= laps)
        loss = np.where(new_red[:, None], 0.0, loss)
        stint = np.where(stopping, np.minimum(stint + 1, stint_ends.shape[2] - 1), stint)
        if early.any():
            # A stop brought forward moves the rest of the plan with it.
            shift = np.where(stopping & ~planned, current_end - lap, 0)
            stint_ends = np.where(
                (np.arange(stint_ends.shape[2])[None, None, :] >= stint[:, :, None])
                & (stint_ends <= laps), stint_ends - shift[:, :, None], stint_ends)
        age = np.where(stopping, 0.0, age) + 1.0

        compound = np.take_along_axis(compounds, stint[:, :, None], axis=2)[:, :, 0]
        compound = np.where(compound < 0, 1, compound)
        # Underlying pace this lap — car, form, fuel, tyre — and the lap as
        # driven, with its noise. Passing is decided on the first: one lucky
        # lap does not get a car past; a real pace advantage does.
        underlying = (typical + pace + profile["fuel_per_lap"] * lap
                      + offset[compound] + wear[compound] * age)
        lap_time = underlying + rng.normal(0.0, lap_noise, size=(runs, n))
        lap_time = np.where(stopping, lap_time + loss, lap_time)
        # Behind a safety car or under a VSC everyone runs the same lap; gaps
        # hold, and only a stop costs anything.
        lap_time = np.where(neutral[:, None], typical * 1.35 + np.where(stopping, loss, 0.0), lap_time)

        order = np.argsort(cumulative, axis=1)
        before = np.take_along_axis(cumulative, order, axis=1)
        times = np.take_along_axis(lap_time, order, axis=1)
        speed = np.take_along_axis(underlying, order, axis=1)
        after = before + times
        green = ~neutral
        # Passing: down the order, each car held behind the one ahead unless
        # its underlying pace is better by more than the threshold.
        for position in range(1, n):
            ahead = after[:, position - 1]
            mine = after[:, position]
            advantage = speed[:, position - 1] - speed[:, position]
            blocked = (mine < ahead + FOLLOW_GAP) & (advantage < threshold) & green
            after[:, position] = np.where(blocked, np.maximum(mine, ahead + FOLLOW_GAP), mine)
        cumulative = np.empty_like(cumulative)
        cumulative[rows, order] = after

        # A safety car closes the field up behind the leader.
        if new_sc.any() or new_red.any():
            bunch = new_sc | new_red
            order = np.argsort(cumulative, axis=1)
            leader = np.take_along_axis(cumulative, order[:, :1], axis=1)
            rank = np.empty_like(order)
            rank[rows, order] = np.arange(n)[None, :]
            closed = leader + rank * BUNCHED_GAP
            cumulative = np.where(bunch[:, None] & np.isfinite(cumulative), closed, cumulative)

        newly_out = (retire_lap == lap) & ~out
        out |= newly_out
        cumulative = np.where(out, np.inf, cumulative)
        sc_left = np.maximum(sc_left - 1, 0)
        vsc_left = np.maximum(vsc_left - 1, 0)

    if start is not None:
        cumulative = cumulative + start.penalty[None, :]
    # Finishers by time; retirements behind them, the later the better.
    rank_key = np.where(out, 1e12 - retire_lap.astype(float), cumulative)
    order = np.argsort(rank_key, axis=1, kind="stable")
    positions = np.empty_like(order)
    positions[rows, order] = np.arange(1, n + 1)[None, :]
    return positions, out


class LapRaceEngine:
    """The simulator with its fitted profiles, ready to run a race.

    ``enabled`` is the artifact's verdict from ``scripts/fit_lap_simulation.py``:
    true only if, on the held-out seasons, the simulator scored no worse than
    the Plackett-Luce sampler on the published markets. Loaded either way so
    the verdict can be reported; used only when enabled.
    """

    def __init__(self, profiles: Dict, params: LapSimulationParameters,
                 enabled: bool = False, metrics: Optional[Dict[str, Any]] = None,
                 version: str = LAP_ENGINE_VERSION, pace=None) -> None:
        self.profiles = profiles
        self.params = params
        self.enabled = enabled
        self.metrics = metrics or {}
        self._version = version
        # The lap-trained pace network, when its artifact says it beat the
        # model's strengths season by season. Each car's pace then comes from
        # it, in seconds, instead of from the race model's strength.
        self.pace = pace

    @property
    def version(self) -> str:
        if self.pace is not None:
            return "{}+{}".format(self._version, self.pace["version"])
        return self._version

    @classmethod
    def load(cls, path: str = LAP_SIMULATION_PATH) -> "LapRaceEngine":
        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            raise ModelWeightsMissing(
                "no lap simulation profiles at {}; run "
                "scripts/fit_lap_simulation.py".format(resolved))
        with open(resolved) as handle:
            artifact = json.load(handle)
        logger.info("loaded lap simulator %s fitted %s (enabled=%s)",
                    artifact.get("version"), artifact.get("fitted_at"),
                    artifact.get("enabled"))
        pace = load_pace_network()
        metrics = dict(artifact.get("metrics", {}))
        if pace is not None:
            metrics["pace_network"] = pace["metrics"]
        return cls(
            profiles=artifact["profiles"],
            params=LapSimulationParameters.from_dict(artifact["parameters"]),
            enabled=bool(artifact.get("enabled", False)),
            metrics=metrics,
            version=artifact.get("version", LAP_ENGINE_VERSION),
            pace=pace,
        )

    def profile(self, circuit: str) -> Dict:
        return self.profiles["circuits"].get(
            circuits.normalise(circuit), self.profiles["overall"])

    def sample_outcomes(self, model, drivers: Sequence[DriverFeatures], seed: int,
                        runs: int, circuit: str = "",
                        distance_fraction: float = 1.0):
        """``model``'s strengths and retirement clustering, raced lap by lap.

        With an enabled pace network, the strengths come from its predicted
        pace instead, converted to the same scale.
        """
        profile = self.profile(circuit)
        params = self.params
        strengths = model.strengths(drivers)
        if self.pace is not None and drivers:
            network = self.pace["network"]
            x = np.vstack([feature_vector(d, with_grid=True) for d in drivers])
            predicted = network.predict(x, np.zeros(len(drivers)))
            seconds = predicted / 100.0 * profile["typical_lap"]
            strengths = -self.pace["scale"] * seconds / params.seconds_per_strength
            params = LapSimulationParameters.from_dict(
                dict(params.to_dict(), race_day_sd=self.pace["race_day_sd"]))
        return simulate(
            drivers, strengths, profile, params,
            seed=seed, runs=runs,
            retirement_race_k=getattr(model, "retirement_race_k", None),
            retirement_team_k=getattr(model, "retirement_team_k", None),
            distance_fraction=distance_fraction,
        )


def load_pace_network(path: str = PACE_NETWORK_PATH) -> Optional[Dict[str, Any]]:
    """The pace network and its settings, only if its artifact is enabled."""
    from app.training.pace_network import PaceNetwork

    resolved = os.path.abspath(path)
    if not os.path.exists(resolved):
        return None
    with open(resolved) as handle:
        artifact = json.load(handle)
    if not artifact.get("enabled"):
        logger.info("pace network present but not enabled: %s",
                    artifact.get("metrics", {}).get("verdict"))
        return None
    return {
        "network": PaceNetwork.from_dict(artifact["network"]),
        "scale": float(artifact["pace_scale"]),
        "race_day_sd": float(artifact["race_day_sd"]),
        "version": artifact.get("version", "pace-v1"),
        "metrics": artifact.get("metrics", {}),
    }
