"""The finishing order forecast during a race, from the race as it stands.

Before the start, the forecast is the pre-race one: the lap simulator raced
from the grid with each car's pace from the race model. Once the race is on,
``forecast`` restarts the same simulator from the real race at the end of the
latest lap (``live_state.race_state``): the order and gaps on the road, the
tyres each car is on and how old they are, the stops already made, the cars
already out, and any safety car or VSC still out. The simulator then races the
laps that are left, thousands of times.

**Pace is updated from the race** (``posterior_pace``). Each car's pre-race
pace is a prior; its corrected lap times so far this race are evidence, and
the two are blended with weights in the ratio ``prior_laps`` to clean laps
run. After ``prior_laps`` clean laps a car's own running counts as much as
everything known before the start; after three times that, three times as
much. What is left of the uncertainty about a car's pace shrinks the same way.
``prior_laps`` is fitted by the replay backtest (``scripts/backtest_live.py``),
not picked by hand.

Nothing here changes the locked pre-race forecast, which is what the track
record scores.
"""

from typing import Dict, List, Optional, Sequence

import numpy as np

from app.models.schemas import DriverFeatures
from app.services.lap_simulation import LapSimulationParameters, LiveStart, simulate
from app.services.live_state import RaceState

#: Clean laps of a car's own running worth as much as its pre-race pace.
#: Replaced by the backtest's fitted value when there is one.
DEFAULT_PRIOR_LAPS = 10.0
#: Points-paying places.
POINTS_PLACES = 10


def prior_pace(strengths: np.ndarray, params: LapSimulationParameters) -> np.ndarray:
    """The race model's strengths as seconds per lap against the field average,
    negative = quicker: the same conversion the simulator makes from the grid."""
    strengths = np.asarray(strengths, float)
    return -(strengths - strengths.mean()) * params.seconds_per_strength


def posterior_pace(prior: np.ndarray, state: RaceState, race_day_sd: float,
                   prior_laps: float = DEFAULT_PRIOR_LAPS):
    """(pace, remaining spread) per car, seconds per lap.

    The observed figures are relative to the median car on each lap, the prior
    to the field average; the observed ones are shifted so that, over the cars
    that have run clean laps, both average the same. Only differences between
    cars matter to the simulator.
    """
    prior = np.asarray(prior, float)
    k = state.observed_laps.astype(float)
    seen = k > 0
    observed = state.observed_pace.copy()
    if seen.any():
        weights = k[seen]
        shift = (np.average(prior[seen], weights=weights)
                 - np.average(observed[seen], weights=weights))
        observed[seen] += shift
    weight = k / (k + prior_laps)
    pace = (1.0 - weight) * prior + weight * np.where(seen, observed, prior)
    spread = race_day_sd * np.sqrt(prior_laps / (k + prior_laps))
    return pace, spread


def live_start(state: RaceState, pace: np.ndarray, spread: np.ndarray) -> LiveStart:
    return LiveStart(
        lap=state.lap, total_laps=state.total_laps, gaps=state.gaps,
        retired=state.retired, retired_lap=state.retired_lap,
        compound=state.compound_index(), tyre_age=state.tyre_age,
        stops=state.stops, used=state.used, pace=pace, pace_sd=spread,
        safety_car_laps=state.safety_car_laps, vsc_laps=state.vsc_laps,
        wet=state.wet,
    )


def forecast(
    drivers: Sequence[DriverFeatures],
    strengths: np.ndarray,
    profile: Dict,
    params: LapSimulationParameters,
    state: Optional[RaceState],
    seed: int,
    runs: int,
    prior_laps: float = DEFAULT_PRIOR_LAPS,
    retirement_race_k: Optional[float] = None,
    retirement_team_k: Optional[float] = None,
    pace_override: Optional[np.ndarray] = None,
):
    """(positions, retired), each (runs, n), from ``state`` on; from the grid
    when ``state`` is None or no lap has been completed.

    ``pace_override`` (seconds per lap per car, NaN where it has nothing) takes
    the place of the posterior pace for the cars it covers, shifted to the
    posterior's level over those cars: the lap model's view of each car
    (``app/services/lap_model.py``). The spread is left as it is."""
    if state is None or state.lap < 1:
        if state is not None:
            profile = dict(profile, laps=state.total_laps)
        return simulate(drivers, strengths, profile, params, seed=seed, runs=runs,
                        retirement_race_k=retirement_race_k,
                        retirement_team_k=retirement_team_k)
    names = [d.driver for d in drivers]
    if names != state.drivers:
        raise ValueError("race state drivers are not in the forecast's order")
    pace, spread = posterior_pace(prior_pace(strengths, params), state,
                                  params.race_day_sd, prior_laps)
    pace = apply_override(pace, pace_override, state.retired)
    return simulate(drivers, strengths, profile, params, seed=seed, runs=runs,
                    retirement_race_k=retirement_race_k,
                    retirement_team_k=retirement_team_k,
                    start=live_start(state, pace, spread))


def apply_override(pace: np.ndarray, override: Optional[np.ndarray],
                   retired: np.ndarray) -> np.ndarray:
    """``pace`` with ``override`` where it is finite, on the same level."""
    if override is None:
        return pace
    override = np.asarray(override, float)
    use = np.isfinite(override) & ~np.asarray(retired, bool)
    if use.sum() < 2:
        return pace
    shifted = override - override[use].mean() + pace[use].mean()
    return np.where(use, shifted, pace)


def summarise(drivers: Sequence[DriverFeatures], positions: np.ndarray,
              retired: np.ndarray) -> List[Dict]:
    """Per driver: chances of winning, a podium, points, retiring, and the
    expected finishing place. Sorted by expected place."""
    rows = []
    for i, d in enumerate(drivers):
        place = positions[:, i]
        finished = ~retired[:, i]
        rows.append({
            "driver": d.driver,
            "team": d.team,
            "win": float(((place == 1) & finished).mean()),
            "podium": float(((place <= 3) & finished).mean()),
            "points": float(((place <= POINTS_PLACES) & finished).mean()),
            "retire": float(retired[:, i].mean()),
            "expected_position": float(place.mean()),
        })
    rows.sort(key=lambda r: r["expected_position"])
    return rows
