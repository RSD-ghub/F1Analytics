"""What a Grand Prix at each circuit is like, measured from its laps.

The lap-by-lap simulator (``services/lap_simulation.py``) needs to know, for the
circuit it is simulating: how many laps, how fast a lap is, what fuel burn and
each tyre compound do to a lap time, how long a pit stop costs, how often the
safety car and the VSC come out, what strategies teams actually run there, how
much the order shuffles on the first lap, and how hard it is to pass.

Every one of those is measured per race from 2018 (tyres and strategies from
2019, when the compounds took their current names), then pooled per circuit
and shrunk toward the all-circuit value in proportion to how few races support
it — three races at a street circuit should not set its safety-car rate alone.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats

from app.services import circuits, race_pace
from app.services.ingestion_client import (
    PitStop,
    RaceControlMessage,
    RaceLap,
    RaceResult,
    Stint,
)

logger = logging.getLogger(__name__)

DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
#: Compounds took their current names in 2019. 2018's hypersoft-to-superhard
#: range does not map onto them race by race, so tyres start here.
FIRST_NAMED_COMPOUND_SEASON = 2019
#: Pseudo-races of the all-circuit value mixed into each circuit's own.
SHRINKAGE_RACES = 4.0


class RaceContext:
    """One race, summarised for the simulator."""

    def __init__(self, season: int, round_number: int, circuit: str) -> None:
        self.season = season
        self.round = round_number
        self.circuit = circuit
        self.laps = 0
        self.fit: Optional[race_pace.RaceFit] = None
        self.pit_losses: List[float] = []
        self.safety_cars = 0
        self.virtual_safety_cars = 0
        self.red_flags = 0
        #: (compound sequence, stint lengths as fractions of race distance),
        #: for every car that finished.
        self.strategies: List[Tuple[Tuple[str, ...], Tuple[float, ...]]] = []
        #: (grid slot, position after lap 1) for every car with both.
        self.starts: List[Tuple[int, int]] = []
        #: Rank correlation of grid and finish among classified cars.
        self.orderliness: Optional[float] = None


def summarise_race(
    season: int,
    round_number: int,
    laps: Sequence[RaceLap],
    stints: Sequence[Stint],
    pit_stops: Sequence[PitStop],
    messages: Sequence[RaceControlMessage],
    results: Sequence[RaceResult],
) -> RaceContext:
    circuit = next((r.circuit for r in results if r.circuit), "")
    context = RaceContext(season, round_number, circuit)
    context.laps = max((lap.lap for lap in laps), default=0)
    context.fit = race_pace.fit_race(laps)

    # Pit loss: the in-lap and out-lap, against the driver's typical clean lap.
    times = defaultdict(dict)
    for lap in laps:
        if lap.lap_time_seconds > 0:
            times[lap.driver][lap.lap] = lap.lap_time_seconds
    for stop in pit_stops:
        own = times.get(stop.driver, {})
        if stop.lap in own and stop.lap + 1 in own and len(own) >= 10:
            reference = float(np.median(list(own.values())))
            loss = own[stop.lap] + own[stop.lap + 1] - 2 * reference
            if 5.0 < loss < 60.0:  # a drive-through or a repair is not a stop
                context.pit_losses.append(loss)

    for message in messages:
        text = message.message.upper()
        if message.category == "SafetyCar":
            if text.startswith("VIRTUAL SAFETY CAR DEPLOYED"):
                context.virtual_safety_cars += 1
            elif text.startswith("SAFETY CAR DEPLOYED"):
                context.safety_cars += 1
        elif message.category == "Flag" and text.startswith("RED FLAG"):
            context.red_flags += 1

    classified = {r.driver for r in results if r.classified}
    if season >= FIRST_NAMED_COMPOUND_SEASON and context.laps:
        by_driver = defaultdict(list)
        for stint in stints:
            by_driver[stint.driver].append(stint)
        for driver, rows in by_driver.items():
            rows.sort(key=lambda s: s.stint)
            compounds = tuple((s.compound or "").upper() for s in rows)
            if driver not in classified or not all(c in DRY_COMPOUNDS for c in compounds):
                continue
            total = sum(s.laps for s in rows)
            if total < 0.9 * context.laps:
                continue
            context.strategies.append(
                (compounds, tuple(s.laps / total for s in rows)))

    grid = {r.driver: r.grid_position for r in results if r.grid_position > 0}
    first_lap = sorted(
        (lap.lap_time_seconds, lap.driver) for lap in laps
        if lap.lap == 1 and lap.lap_time_seconds > 0 and lap.driver in grid
    )
    order = sorted(grid, key=grid.get)
    slot = {driver: index + 1 for index, driver in enumerate(order)}
    context.starts = [(slot[d], index + 1) for index, (_, d) in enumerate(first_lap)]

    finished = [(r.grid_position, r.position) for r in results
                if r.classified and r.grid_position > 0]
    if len(finished) > 5:
        context.orderliness = float(stats.spearmanr(*zip(*finished))[0])
    return context


def _shrunk(values: Sequence[float], prior: float, k: float = SHRINKAGE_RACES) -> float:
    return (sum(values) + k * prior) / (len(values) + k)


def _median(values, default):
    values = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.median(values)) if values else default


def _tyres(contexts: Sequence[RaceContext]) -> Dict[str, Dict[str, float]]:
    """Compound offset (vs medium) and wear per lap, medians over races."""
    offsets, wear = defaultdict(list), defaultdict(list)
    for c in contexts:
        if c.fit is None or c.season < FIRST_NAMED_COMPOUND_SEASON:
            continue
        if "MEDIUM" not in c.fit.compound_offset:
            continue
        base = c.fit.compound_offset["MEDIUM"]
        for compound in DRY_COMPOUNDS:
            if compound in c.fit.compound_offset:
                offsets[compound].append(c.fit.compound_offset[compound] - base)
                wear[compound].append(c.fit.wear_per_lap[compound])
    return {
        compound: {
            "offset": _median(offsets[compound], 0.0),
            "wear": _median(wear[compound], 0.05),
            "races": len(offsets[compound]),
        }
        for compound in DRY_COMPOUNDS
    }


def _profile(contexts: Sequence[RaceContext], prior: Optional[Dict] = None) -> Dict:
    fits = [c.fit for c in contexts if c.fit is not None]
    races = len(contexts)
    profile = {
        "races": races,
        "laps": int(round(_median([c.laps for c in contexts if c.laps], 57))),
        "typical_lap": _median([f.typical_lap for f in fits], 90.0),
        "fuel_per_lap": _median([f.fuel_per_lap for f in fits], -0.06),
        "lap_noise": _median([f.lap_noise for f in fits], 0.4),
        "pit_loss": _median([x for c in contexts for x in c.pit_losses], 22.0),
        "tyres": _tyres(contexts),
        "strategies": [
            {"compounds": list(compounds), "fractions": [round(f, 4) for f in fractions]}
            for c in contexts for compounds, fractions in c.strategies
        ],
        "orderliness": _median([c.orderliness for c in contexts], 0.75),
    }
    rates = {
        "safety_cars_per_race": [float(c.safety_cars) for c in contexts],
        "vscs_per_race": [float(c.virtual_safety_cars) for c in contexts],
        "red_flags_per_race": [float(min(c.red_flags, 1)) for c in contexts],
    }
    orderliness = [c.orderliness for c in contexts if c.orderliness is not None]
    if prior is None:
        for key, values in rates.items():
            profile[key] = float(np.mean(values)) if values else 0.0
    else:
        for key, values in rates.items():
            profile[key] = _shrunk(values, prior[key])
        profile["orderliness"] = _shrunk(orderliness, prior["orderliness"])
        losses = [x for c in contexts for x in c.pit_losses]
        if losses:
            profile["pit_loss"] = (
                races * _median(losses, prior["pit_loss"])
                + SHRINKAGE_RACES * prior["pit_loss"]
            ) / (races + SHRINKAGE_RACES)
        else:
            profile["pit_loss"] = prior["pit_loss"]
        if len(fits) < 2:
            for key in ("typical_lap", "fuel_per_lap", "lap_noise"):
                profile[key] = prior[key]
        for compound, row in profile["tyres"].items():
            if row["races"] < 2:
                profile["tyres"][compound] = dict(prior["tyres"][compound])
        if len(profile["strategies"]) < 15:
            profile["strategies"] = prior["strategies"]
    return profile


def start_shuffle(contexts: Sequence[RaceContext]) -> float:
    """Spread of first-lap position change, in places, over all starts."""
    changes = [lap1 - slot for c in contexts for slot, lap1 in c.starts]
    return float(np.std(changes)) if changes else 2.0


def build_profiles(contexts: Sequence[RaceContext]) -> Dict:
    """All-circuit profile plus one per circuit, keyed by normalised name."""
    overall = _profile(contexts)
    by_circuit = defaultdict(list)
    for c in contexts:
        by_circuit[circuits.normalise(c.circuit)].append(c)
    return {
        "overall": overall,
        "start_shuffle": start_shuffle(contexts),
        "circuits": {
            name: _profile(group, prior=overall)
            for name, group in sorted(by_circuit.items()) if name
        },
    }


def profile_for(profiles: Dict, circuit: str) -> Dict:
    """The circuit's profile, or the all-circuit one for a track never raced."""
    return profiles["circuits"].get(circuits.normalise(circuit), profiles["overall"])
