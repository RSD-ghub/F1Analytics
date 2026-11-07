"""A finished race replayed lap by lap, as the live forecast would have seen it.

For each lap of a past Grand Prix the race state is rebuilt from the timing
rows up to that lap only (``live_state.race_state``) and the live forecast is
run from it, exactly as the replay backtest does. The result is a timeline the
Live page can play back: who was where, on what tyres, and how the chances of
winning and of a podium moved, lap by lap, ending with the real result.

The live network (``training/live_network.py``) corrects the win and podium
chances when its artifact says it passed its gate, and never in the wet: it
was trained on dry races and scored worse on the five wet ones.

Nothing here writes anything. A replay is computed on request and cached in
memory by the caller.
"""

import json
import logging
import os
from functools import lru_cache
from typing import Dict, List, Optional, Sequence

import numpy as np

from app.services import live_forecast
from app.services.ingestion_client import PitStop, RaceControlMessage, RaceLap, RaceResult
from app.services.lap_simulation import LapSimulationParameters
from app.services.live_state import WET_COMPOUNDS, RaceState, race_state
from app.services.model import LIVE_NETWORK_PATH

logger = logging.getLogger(__name__)

#: Clean laps of a car's own running worth as much as its pre-race pace. Chosen
#: on 2022-23 by ``scripts/backtest_live.py`` (candidates 1, 3, 10, 30).
REPLAY_PRIOR_LAPS = 3.0
#: Simulated races per lap. Fewer than the 1,000 the backtest scored with, so
#: a whole race replays in seconds; a 300-run chance moves by about 2-3 points
#: between neighbouring laps from sampling alone.
REPLAY_RUNS = 300
PODIUM = 3


def load_live_network(path: str = LIVE_NETWORK_PATH):
    """(network, artifact) when the artifact exists and passed its gate;
    (None, artifact or None) otherwise."""
    from app.training.live_network import LiveNetwork

    resolved = os.path.abspath(path)
    if not os.path.exists(resolved):
        return None, None
    with open(resolved) as handle:
        artifact = json.load(handle)
    if not artifact.get("enabled"):
        logger.info("live network present but not enabled")
        return None, artifact
    return LiveNetwork.from_dict(artifact["network"]), artifact


@lru_cache(maxsize=1)
def cached_live_network():
    """``load_live_network`` once per process; a broken artifact means none."""
    try:
        return load_live_network()
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("live network unreadable, replaying without it: %s", exc)
        return None, None


def total_laps_of(laps: Sequence[RaceLap], results: Sequence[RaceResult]) -> int:
    """The race distance: the winner's lap count, else the most laps anyone did."""
    winner = next((r.driver for r in results if r.position == 1 and r.classified), None)
    own = [lap.lap for lap in laps if lap.driver == winner]
    return max(own or [lap.lap for lap in laps] or [0])


def actual_places(names: Sequence[str], results: Sequence[RaceResult]) -> List[int]:
    """Each driver's classified place, 999 if not classified or not found."""
    by_driver = {r.driver: r for r in results}
    out = []
    for name in names:
        row = by_driver.get(name)
        out.append(row.position if row is not None and row.classified else 999)
    return out


def _flag(state: RaceState, messages: Sequence[RaceControlMessage]) -> str:
    """What was out on track at the end of the lap: SC, VSC, RED or ''."""
    on_lap = [(m.message or "").upper() for m in messages if m.lap == state.lap]
    if any("RED FLAG" in m for m in on_lap):
        return "RED"
    if state.safety_car_laps > 0:
        return "SC"
    if state.vsc_laps > 0:
        return "VSC"
    return ""


def _frame(lap: int, names, teams, positions, retired, state: Optional[RaceState],
           flag: str, win=None, podium=None, corrected=False) -> Dict:
    """One lap of the timeline: the cars in running order, with their chances."""
    sim_win = ((positions == 1) & ~retired).mean(axis=0)
    sim_podium = ((positions <= PODIUM) & ~retired).mean(axis=0)
    points = ((positions <= live_forecast.POINTS_PLACES) & ~retired).mean(axis=0)
    expected = positions.mean(axis=0)
    win = sim_win if win is None else win
    podium = sim_podium if podium is None else podium
    if state is not None:
        order = state.running_order()
    else:
        order = [names[i] for i in np.argsort(expected, kind="stable")]
    index = {n: i for i, n in enumerate(names)}
    cars = []
    for place, name in enumerate(order, start=1):
        i = index[name]
        car = {
            "driver": name,
            "team": teams[i],
            "position": place,
            "win": round(float(win[i]), 4),
            "podium": round(float(podium[i]), 4),
            "points": round(float(points[i]), 4),
            "expected_position": round(float(expected[i]), 2),
            "gap": None, "compound": None, "tyre_age": 0, "stops": 0, "retired": False,
        }
        if state is not None:
            car.update(
                gap=None if state.retired[i] else round(float(state.gaps[i]), 3),
                compound=state.compound[i] or None,
                tyre_age=int(state.tyre_age[i]),
                stops=int(state.stops[i]),
                retired=bool(state.retired[i]),
            )
        cars.append(car)
    return {"lap": lap, "flag": flag, "corrected": corrected, "cars": cars}


def replay_laps(total: int, step: int) -> List[int]:
    """0 (the grid), every ``step`` laps, and the last lap before the flag."""
    last = max(total - 1, 0)
    laps = list(range(0, last + 1, max(step, 1)))
    if laps[-1] != last:
        laps.append(last)
    return laps


def build_replay(
    drivers,
    strengths: np.ndarray,
    profile: Dict,
    params: LapSimulationParameters,
    laps: Sequence[RaceLap],
    pit_stops: Sequence[PitStop],
    messages: Sequence[RaceControlMessage],
    results: Sequence[RaceResult],
    seed: int,
    runs: int = REPLAY_RUNS,
    step: int = 1,
    prior_laps: float = REPLAY_PRIOR_LAPS,
    network=None,
    retirement_race_k: Optional[float] = None,
    retirement_team_k: Optional[float] = None,
) -> Dict:
    """The whole timeline of one finished race. ``drivers`` are the race
    model's features for the field."""
    from app.training import live_network

    names = [d.driver for d in drivers]
    teams = [d.team for d in drivers]
    total = total_laps_of(laps, results)
    if total < 2:
        raise ValueError("no lap timing for this race")
    wet = any((lap.compound or "").upper() in WET_COMPOUNDS for lap in laps)
    kwargs = dict(retirement_race_k=retirement_race_k, retirement_team_k=retirement_team_k)
    frames = []
    for lap in replay_laps(total, step):
        if lap < 1:
            positions, retired = live_forecast.forecast(
                drivers, strengths, dict(profile, laps=total), params, None,
                seed=seed, runs=runs, **kwargs)
            frames.append(_frame(0, names, teams, positions, retired, None, ""))
            continue
        state = race_state(names, laps, pit_stops, messages, results,
                           upto=lap, total_laps=total, profile=profile)
        positions, retired = live_forecast.forecast(
            drivers, strengths, profile, params, state, seed=seed + lap, runs=runs,
            prior_laps=prior_laps, **kwargs)
        win = podium = None
        corrected = False
        if network is not None and not state.wet:
            pace, _ = live_forecast.posterior_pace(
                live_forecast.prior_pace(strengths, params), state,
                params.race_day_sd, prior_laps)
            sim_win = ((positions == 1) & ~retired).mean(axis=0)
            sim_podium = ((positions <= PODIUM) & ~retired).mean(axis=0)
            x = live_network.features(state, pace, positions.mean(axis=0),
                                      retired.mean(axis=0), profile)
            win, podium = network.predict(x, sim_win, sim_podium, ~state.retired)
            corrected = True
        frames.append(_frame(lap, names, teams, positions, retired, state,
                             _flag(state, messages), win, podium, corrected))

    actual = actual_places(names, results)
    by_driver = {r.driver: r for r in results}
    result = sorted(
        ({"driver": n, "team": t, "position": a if a < 999 else None,
          "status": by_driver[n].status if n in by_driver else ""}
         for n, t, a in zip(names, teams, actual)),
        key=lambda r: (r["position"] is None, r["position"] or 0))
    return {
        "total_laps": total,
        "wet": wet,
        "runs": runs,
        "prior_laps": prior_laps,
        "network": network is not None,
        "frames": frames,
        "result": result,
    }
