"""The strategy engine: a race plan built the way the strategy guide builds one.

``strategy.py`` answers the first question a pit wall asks — what is the
quickest race for one car alone on the track? The guide (``How to Win a Grand
Prix``, chapter 15) is clear that this is only the starting point: "Then we set
that aside." The engine takes the next steps the book lays out, in its order:

1. **What kind of race is it?** Where overtaking is hard, track position is
   worth more than the seconds a quicker plan saves, and the plan moves away
   from the single-car optimum — usually a stop fewer. Where overtaking is easy
   and tyres wear, the plan stays close to the optimum.
2. **Plans A, B and C, plus S for the safety car**, with stop laps a driver can
   remember: a target on a round number inside the window rather than the
   optimum to the lap (chapter 21).
3. **The start tyre**, which depends on where the car starts: protect the lead
   off the line, gain places off it, or start on the harder tyre and run long
   hoping a safety car makes the stop cheap (chapters 15 and 20).
4. **The safety-car window**: from which lap a neutralisation is worth taking
   the stop early, and when late in the race it pays for an extra one
   (chapter 22).
5. **Among nineteen other cars**: where the car rejoins after its stop, who it
   comes out behind, how many laps longer it must run to clear them, and
   whether the undercut works on the car ahead or threatens from behind
   (chapters 15 and 22). Plus the two edge cases the book asks for: box while
   everyone stays out, stay out while everyone boxes.

Each section names the question it answers in the guide's words
(``guide_query``) so the caller can put the passage that argues for it beside
it. The engine never needs the guide to decide anything: the rules are in code,
and the passages are the citation.

Deterministic and pure. Same inputs, same plan; no I/O here.
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.strategy import (
    COMPOUNDS,
    MIN_STINT_LAPS,
    OUT_LAP_WARMUP_SECONDS,
    SAFETY_CAR_LOSS_SHARE,
    SHORT,
    Inputs,
    Plan,
    Simulation,
)

#: Seconds of simulated race time worth giving up for one stop fewer, by how
#: hard it is to overtake. A stop costs places on track; where those places
#: cannot be won back, a slower plan with fewer stops finishes higher.
TRACK_POSITION_TOLERANCE = {"hard": 8.0, "moderate": 3.0, "easy": 0.0}

#: Overtaking thresholds on mean places changed between grid and flag. Monaco
#: runs under two; circuits where the order shuffles freely run above three and
#: a half.
HARD_TO_PASS_PLACES = 2.5
EASY_TO_PASS_PLACES = 3.5
#: A pole-sitter winning this often says the same thing from the other side.
HARD_TO_PASS_POLE_RATE = 0.6

#: Soft wear above this (seconds per lap, per lap of age) is a high-degradation
#: circuit, where fresh tyres make passing easier and the optimum matters more.
HIGH_DEGRADATION = 0.09

#: How far apart cars are, per grid slot, once the opening laps have spread the
#: field. A planning figure, labelled as one.
SPREAD_PER_PLACE = 0.6

#: Margin to rejoin ahead of a car rather than alongside it.
CLEAR_MARGIN = 1.0

#: Gap ahead after a stop that counts as clear air.
CLEAR_AIR = 2.0

#: A safety car this close to the flag will not see a restart worth stopping for.
NO_RESTART_LAPS = 3

#: Target stop laps land on multiples of this, when one sits inside the window
#: and within two laps of the optimum.
MEMORABLE = 5
MEMORABLE_REACH = 2

#: Grid zones for the start-tyre call.
FRONT_ROW = 2
BACK_OF_GRID = 15
SAFETY_CAR_WORTH_WAITING = 0.3

HARDNESS = {c: i for i, c in enumerate(COMPOUNDS)}


# ── The field ────────────────────────────────────────────────────────────────


@dataclass
class Car:
    driver: str
    team: str
    grid: int
    #: Quickest practice long run, seconds per lap. None when not measured.
    pace: Optional[float] = None


def build_field(
    qualifying: Iterable[Dict[str, Any]], practice: Iterable[Dict[str, Any]] = ()
) -> List[Car]:
    """Cars in starting order, with their practice long-run pace where known.

    The confirmed grid where it exists, the qualifying order otherwise: the
    engine plans from where a car starts, and the classification stands in only
    until the grid is published.
    """
    pace: Dict[str, float] = {}
    for row in practice or []:
        if not isinstance(row, dict):
            continue
        seconds = row.get("long_run_seconds") or 0
        if seconds > 0 and row.get("driver"):
            held = pace.get(row["driver"])
            pace[row["driver"]] = seconds if held is None else min(held, seconds)

    cars = []
    for row in qualifying or []:
        if not isinstance(row, dict) or not row.get("driver"):
            continue
        slot = row.get("grid_position") or 0
        if slot <= 0:
            slot = row.get("position") or 999
        if slot >= 999:
            continue
        cars.append(Car(row["driver"], row.get("team") or "?", int(slot),
                        pace.get(row["driver"])))
    cars.sort(key=lambda c: c.grid)
    return cars


def _surname(name: str) -> str:
    return name.split()[-1].casefold() if name.split() else ""


def find_driver(field: Sequence[Car], name: Optional[str]) -> Optional[Car]:
    """A car by full name, surname or fragment, case-insensitively."""
    wanted = (name or "").strip().casefold()
    if not wanted:
        return None
    for match in (
        lambda c: c.driver.casefold() == wanted,
        lambda c: _surname(c.driver) == wanted,
        lambda c: wanted in c.driver.casefold(),
    ):
        found = [c for c in field if match(c)]
        if found:
            return found[0]
    return None


def mentioned_driver(field: Sequence[Car], question: str) -> Optional[Car]:
    """The first car whose surname appears in a question, as a whole word."""
    text = (question or "").casefold()
    best: Optional[Tuple[int, Car]] = None
    for car in field:
        surname = _surname(car.driver)
        if len(surname) < 3:
            continue
        match = re.search(r"\b{}\b".format(re.escape(surname)), text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), car)
    return best[1] if best else None


# ── What kind of race ────────────────────────────────────────────────────────


def race_character(stats: Optional[Dict[str, Any]], inputs: Inputs) -> Dict[str, Any]:
    """Track-position race or optimum-strategy race (guide, chapter 15)."""
    stats = stats or {}
    places = stats.get("mean_places_changed")
    pole_rate = stats.get("pole_win_rate")
    if pole_rate is None and (stats.get("pole_starts") or 0) >= 3:
        pole_rate = (stats.get("pole_wins") or 0) / stats["pole_starts"]

    reasons: List[str] = []
    if places is not None:
        reasons.append("cars here gain or lose {} places on average between grid and "
                       "flag".format(places))
    if pole_rate is not None:
        reasons.append("pole has won {:.0%} of races here".format(pole_rate))

    if places is None and pole_rate is None:
        overtaking = "moderate"
        reasons.append("GENERIC: no overtaking history is held for this circuit, so it "
                       "is planned as an average one")
    elif (places is not None and places < HARD_TO_PASS_PLACES) or (
        pole_rate is not None and pole_rate >= HARD_TO_PASS_POLE_RATE
    ):
        overtaking = "hard"
    elif places is not None and places >= EASY_TO_PASS_PLACES and (
        pole_rate is None or pole_rate < 0.5
    ):
        overtaking = "easy"
    else:
        overtaking = "moderate"

    soft = inputs.tyres["SOFT"].degradation
    high_wear = soft >= HIGH_DEGRADATION
    reasons.append("the soft loses {:.2f}s a lap for every lap of age{}".format(
        soft, ", which is high" if high_wear else ""))

    if overtaking == "hard":
        kind = "track position"
        summary = ("A track-position race: overtaking is hard here, so holding places "
                   "is worth more than the seconds a quicker plan saves. Expect a stop "
                   "fewer, or a stop timed around traffic, rather than the optimum.")
    elif overtaking == "easy" or (overtaking == "moderate" and high_wear):
        kind = "optimum strategy"
        summary = ("An optimum-strategy race: cars can pass{}, so the plan should "
                   "stay close to the quickest race for a car on its own and rely on "
                   "fresh tyres to win places back.".format(
                       " and tyres wear" if high_wear else ""))
    else:
        kind = "balanced"
        summary = ("Between the two: overtaking is possible but not free, so the "
                   "optimum stands unless a slower plan saves a stop for little time.")
    return {
        "kind": kind,
        "overtaking": overtaking,
        "summary": summary,
        "reasons": reasons,
        "guide_query": "track position important difficult to overtake optimum strategy race",
    }


# ── Plans ────────────────────────────────────────────────────────────────────


@dataclass
class Oriented:
    """A plan with its stints in a chosen order. Race time does not depend on
    the order in this model, so reversing a plan keeps its time and mirrors its
    stop laps and windows."""

    plan: Plan
    sequence: Tuple[str, ...]
    pit_laps: List[int]
    windows: List[Tuple[int, int]]

    @classmethod
    def of(cls, plan: Plan, laps: int, hardest_first: bool = False) -> "Oriented":
        if not hardest_first or plan.sequence[0] == plan.sequence[-1]:
            return cls(plan, tuple(plan.sequence), list(plan.pit_laps), list(plan.windows))
        return cls(
            plan,
            tuple(reversed(plan.sequence)),
            sorted(laps - lap for lap in plan.pit_laps),
            [(laps - hi, laps - lo) for lo, hi in reversed(plan.windows)],
        )

    @property
    def name(self) -> str:
        return "-".join(SHORT[c] for c in self.sequence)


def target_lap(optimum: int, window: Tuple[int, int]) -> int:
    """A stop lap a driver can remember: the nearest round number in reach.

    The guide's own example: an optimum of lap 18 becomes a Plan A target of
    lap 20, worked to as "Plan A, minus two" if the race calls for it.
    """
    lo, hi = window
    rounds = [
        lap for lap in range(lo, hi + 1)
        if lap % MEMORABLE == 0 and abs(lap - optimum) <= MEMORABLE_REACH
    ]
    return min(rounds, key=lambda lap: (abs(lap - optimum), lap)) if rounds else optimum


def _stints(laps: int, oriented: Oriented) -> List[Dict[str, Any]]:
    bounds = [0] + oriented.pit_laps + [laps]
    return [
        {"compound": compound, "from_lap": bounds[i] + 1, "to_lap": bounds[i + 1],
         "laps": bounds[i + 1] - bounds[i]}
        for i, compound in enumerate(oriented.sequence)
    ]


def _describe(label: str, oriented: Oriented, laps: int, sim: Simulation,
              reference: float, why: str) -> Dict[str, Any]:
    plan = oriented.plan
    stops = [
        {"optimum_lap": optimum, "target_lap": target_lap(optimum, window),
         "window": list(window)}
        for optimum, window in zip(oriented.pit_laps, oriented.windows)
    ]
    return {
        "label": label,
        "name": oriented.name,
        "stops": plan.stops,
        "sequence": list(oriented.sequence),
        "stints": _stints(laps, oriented),
        "pit_stops": stops,
        "expected_delta_seconds": round(sim.expected[plan.name] - reference, 1),
        "quickest_share": round(sim.wins.get(plan.name, 0.0), 3),
        "why": why,
    }


def _race_time(inputs: Inputs, sequence: Sequence[str], pit_laps: Sequence[int],
               losses: Sequence[float]) -> float:
    total, start = 0.0, 0
    for compound, end in zip(sequence, list(pit_laps) + [inputs.race_laps]):
        total += inputs.tyres[compound].cumulative[end - start]
        start = end
    return total + sum(losses)


def safety_car_window(inputs: Inputs, oriented: Oriented) -> Dict[str, Any]:
    """From which lap a safety car is worth taking each stop under (chapter 22).

    The window for a stop opens at the earliest lap where pitting under a
    safety car — half the usual loss — is no slower over the race than keeping
    to the plan under green. Late in the race, after the last planned stop, a
    safety car can also pay for an extra stop onto new softs.
    """
    laps, loss = inputs.race_laps, inputs.pit_loss
    seq, pits = oriented.sequence, oriented.pit_laps
    planned = _race_time(inputs, seq, pits, [loss] * len(pits))

    windows = []
    for j, lap in enumerate(pits):
        previous = pits[j - 1] if j else 0
        following = pits[j + 1] if j + 1 < len(pits) else laps
        opens = lap
        for early in range(previous + MIN_STINT_LAPS, lap):
            if following - early < MIN_STINT_LAPS:
                continue
            moved = list(pits)
            moved[j] = early
            losses = [loss] * len(pits)
            losses[j] = loss * SAFETY_CAR_LOSS_SHARE
            if _race_time(inputs, seq, moved, losses) <= planned:
                opens = early
                break
        target = target_lap(lap, oriented.windows[j])
        windows.append({"stop": j + 1, "opens_lap": min(opens, target), "planned_lap": target})

    # After the last stop: does a safety car buy an extra stop onto new softs?
    last = pits[-1] if pits else 0
    worn, fresh = inputs.tyres[seq[-1]], inputs.tyres["SOFT"]
    extra_from = None
    for lap in range(last + MIN_STINT_LAPS, laps - NO_RESTART_LAPS + 1):
        age, remaining = lap - last, laps - lap
        stay = worn.cumulative[age + remaining] - worn.cumulative[age]
        go = fresh.cumulative[remaining] + loss * SAFETY_CAR_LOSS_SHARE
        if go < stay:
            extra_from = lap
            break

    lines = [
        "Stop {}: the safety car window opens on lap {} (planned lap {}). A safety "
        "car from then on means box straight away.".format(
            w["stop"], w["opens_lap"], w["planned_lap"])
        for w in windows
    ]
    if extra_from is not None:
        lines.append(
            "After the last stop, a safety car from lap {} is worth an extra stop onto "
            "new softs, if a set is left.".format(extra_from))
    lines.append(
        "In the last {} laps a safety car is not worth stopping for: the race may "
        "not restart.".format(NO_RESTART_LAPS))
    return {
        "windows": windows,
        "extra_stop_from_lap": extra_from,
        "summary": lines,
        "safety_car_chance": round(inputs.safety_car_rate, 2),
        "guide_query": "safety car window opens pit stop time lost half",
    }


# ── Start tyre ───────────────────────────────────────────────────────────────


def start_tyre(soft_first: Oriented, grid: Optional[int], character: Dict[str, Any],
               inputs: Inputs) -> Dict[str, Any]:
    """Which compound to start on, by starting position (chapters 15 and 20)."""
    softest, hardest = soft_first.sequence[0], soft_first.sequence[-1]
    long_game = inputs.safety_car_rate >= SAFETY_CAR_WORTH_WAITING and softest != hardest

    if grid is None:
        return {
            "compound": softest,
            "hardest_first": False,
            "why": ("No car chosen. From the front, start on the {s} to keep the place off "
                    "the line; from the midfield the {s} is the best chance to gain places "
                    "at the start; from the back, the {h} and a long first stint gives a "
                    "safety car time to arrive.").format(s=softest.lower(), h=hardest.lower()),
            "guide_query": "start tyre soft gain positions off the line pole",
        }
    if grid <= FRONT_ROW:
        return {
            "compound": softest, "hardest_first": False,
            "why": "From P{}, the quicker {} protects the place off the line; the pace can "
                   "be managed later.".format(grid, softest.lower()),
            "guide_query": "pole not wanting to lose positions off the line fastest tyre",
        }
    if grid >= BACK_OF_GRID and long_game:
        return {
            "compound": hardest, "hardest_first": True,
            "why": "From P{}, start on the {} and run long: with a {:.0%} chance of a "
                   "safety car, a long first stint gives one time to come and make the "
                   "stop cheap.".format(grid, hardest.lower(), inputs.safety_car_rate),
            "guide_query": "hard tyre long first stint back of the grid safety car",
        }
    if character["kind"] == "track position" and long_game and grid > 10:
        return {
            "compound": hardest, "hardest_first": True,
            "why": "From P{} at a track-position circuit, the {} buys flexibility: a long "
                   "first stint, ready for a safety car, rather than a stop that rejoins "
                   "in traffic.".format(grid, hardest.lower()),
            "guide_query": "start on medium go longer hope of picking up a safety car flexibility",
        }
    return {
        "compound": softest, "hardest_first": False,
        "why": "From P{}, the {}: the start is the best overtaking chance of the race, "
               "and the quickest tyre makes the most of it.".format(grid, softest.lower()),
        "guide_query": "soft tyre gain positions off the line start best overtaking opportunity",
    }


# ── Among the field ──────────────────────────────────────────────────────────


def _pace_gap(me: Car, car: Car) -> Optional[float]:
    """Seconds a lap ``car`` is slower than ``me`` on long runs, if both measured."""
    if car.pace is None or me.pace is None:
        return None
    return car.pace - me.pace


def _gap(me: Car, car: Car, lap: int) -> float:
    """Seconds ``car`` is behind ``me`` on ``lap`` (negative: ahead), before stops."""
    return (car.grid - me.grid) * SPREAD_PER_PLACE + (_pace_gap(me, car) or 0.0) * lap


def in_traffic(field: Sequence[Car], me: Car, oriented: Oriented,
               inputs: Inputs) -> Dict[str, Any]:
    """Where the car rejoins after its first stop, and what to do about it."""
    if not oriented.pit_laps:
        return {}
    window, loss = oriented.windows[0], inputs.pit_loss
    lap = target_lap(oriented.pit_laps[0], window)
    others = [c for c in field if c is not me]
    gaps = {c.driver: _gap(me, c, lap) for c in others}
    position = 1 + sum(1 for g in gaps.values() if g < 0)
    jumped = sorted((c for c in others if 0 < gaps[c.driver] < loss),
                    key=lambda c: gaps[c.driver])
    rejoin = position + len(jumped)
    out: Dict[str, Any] = {
        "planned_lap": lap,
        "running_position": position,
        "rejoin_position": rejoin,
        "places_lost": len(jumped),
        "guide_query": "traffic rejoin clear the car pit window laps faster",
    }
    lines = []
    if not jumped:
        lines.append("Stopping on lap {} from P{}, the car rejoins P{} with nobody close: "
                     "clear air.".format(lap, position, rejoin))
    else:
        blocker = jumped[-1]
        ahead_by = loss - gaps[blocker.driver]
        out["rejoins_behind"] = blocker.driver
        out["rejoins_behind_by_seconds"] = round(ahead_by, 1)
        lines.append("Stopping on lap {} from P{}, the car rejoins P{}, {:.1f}s behind "
                     "{}{}.".format(lap, position, rejoin, ahead_by, blocker.driver,
                                    ", in their dirty air" if ahead_by < CLEAR_AIR else ""))
        slower = _pace_gap(me, blocker)
        if slower is not None and slower > 0:
            needed = math.ceil((loss + CLEAR_MARGIN - gaps[blocker.driver]) / slower)
            if lap + needed <= window[1]:
                out["clear_by_stopping_lap"] = lap + needed
                lines.append("{} is {:.2f}s a lap slower on long runs: staying out {} lap{} "
                             "longer (lap {}, still inside the window) rejoins ahead of "
                             "them.".format(blocker.driver, slower, needed,
                                            "" if needed == 1 else "s", lap + needed))
            else:
                lines.append("{} is {:.2f}s a lap slower, but clearing them would take {} "
                             "more laps, past the window (laps {}-{}): pass on track on the "
                             "fresher tyre, or wait for their stop.".format(
                                 blocker.driver, slower, needed, window[0], window[1]))
        elif slower is not None:
            lines.append("{} is not slower on long runs, so waiting does not clear them; "
                         "the fresher tyre has to do it, or their stop does.".format(
                             blocker.driver))
    unknown = [c.driver for c in [me] + others if c.pace is None]
    if unknown:
        lines.append("No practice long run for {}: treated as the same pace.".format(
            ", ".join(unknown[:4]) + (" and others" if len(unknown) > 4 else "")))
    out["summary"] = lines
    return out


def undercut(field: Sequence[Car], me: Car, oriented: Oriented,
             inputs: Inputs) -> Dict[str, Any]:
    """Can the car undercut the one ahead, and can the one behind undercut it?"""
    if len(oriented.sequence) < 2:
        return {}
    lap = target_lap(oriented.pit_laps[0], oriented.windows[0])
    old, new = oriented.sequence[0], oriented.sequence[1]
    gain = (inputs.tyres[old].lap_cost(lap - 1)
            - inputs.tyres[new].lap_cost(0) - OUT_LAP_WARMUP_SECONDS)
    others = [c for c in field if c is not me]
    gaps = {c.driver: _gap(me, c, lap - 1) for c in others}
    ahead = max((c for c in others if gaps[c.driver] < 0),
                key=lambda c: gaps[c.driver], default=None)
    behind = min((c for c in others if gaps[c.driver] > 0),
                 key=lambda c: gaps[c.driver], default=None)

    lines = ["Pitting a lap before a rival around lap {} ({} to {}) is worth about {:.1f}s "
             "on that lap.".format(lap, old.lower(), new.lower(), gain)]
    out: Dict[str, Any] = {"lap": lap, "gain_seconds": round(gain, 1),
                           "guide_query": "undercut car ahead pit window clear stop earlier"}
    if ahead is not None:
        gap = -gaps[ahead.driver]
        viable = 0 < gain and gap < gain
        out["ahead"] = {"driver": ahead.driver, "gap_seconds": round(gap, 1), "viable": viable}
        lines.append("{} is about {:.1f}s ahead: the undercut {}.".format(
            ahead.driver, gap, "is on" if viable else "does not reach"))
    if behind is not None:
        gap = gaps[behind.driver]
        threat = 0 < gain and gap < gain
        out["behind"] = {"driver": behind.driver, "gap_seconds": round(gap, 1), "threat": threat}
        lines.append("{} is about {:.1f}s behind: {}.".format(
            behind.driver, gap,
            "they can undercut, so be ready to cover by stopping first"
            if threat else "out of undercut range"))
    if gain <= 0:
        lines.append("The new tyre is not quicker on its out-lap here: the overcut is "
                     "stronger, so staying out is the better answer to a rival's stop.")
    out["summary"] = lines
    return out


def edge_cases(traffic: Dict[str, Any], oriented: Oriented, inputs: Inputs) -> Dict[str, Any]:
    """The two extremes the guide asks for before a stop (chapter 22)."""
    if not traffic or len(oriented.sequence) < 2:
        return {}
    lap = traffic["planned_lap"]
    old, new = oriented.sequence[0], oriented.sequence[1]
    per_lap = max(0.0, inputs.tyres[old].lap_cost(lap - 1) - inputs.tyres[new].lap_cost(0))
    lost = traffic["places_lost"]
    return {
        "summary": [
            "Box while the cars around stay out: drop {} place{} to P{}, on a tyre about "
            "{:.1f}s a lap quicker.".format(lost, "" if lost == 1 else "s",
                                            traffic["rejoin_position"], per_lap),
            "Stay out while they box: hold P{} on track, but give away about {:.1f}s a lap "
            "to their new tyres, more each lap.".format(traffic["running_position"], per_lap),
        ],
        "guide_query": "stay out but everybody else makes a stop box and everyone else stays out",
    }


# ── The whole plan ───────────────────────────────────────────────────────────

NOT_MODELLED = (
    "Rivals' own stops (gaps assume nobody else has stopped yet), cars held up "
    "behind slower ones, dirty air, weather, each team's tyre allocation, and "
    "pit-stop errors. Field gaps are a planning spread of {:.1f}s a grid place "
    "plus practice long-run pace.".format(SPREAD_PER_PLACE)
)


def plan_race(
    sim: Simulation,
    stats: Optional[Dict[str, Any]] = None,
    field: Sequence[Car] = (),
    driver: Optional[Car] = None,
) -> Dict[str, Any]:
    """The engine's full plan for one car (or the race, with no car named)."""
    if not sim.plans:
        return {}
    inputs = sim.inputs
    laps = inputs.race_laps
    character = race_character(stats, inputs)

    by_expected = sorted(sim.plans, key=lambda p: sim.expected[p.name])
    reference = sim.expected[by_expected[0].name]
    tolerance = TRACK_POSITION_TOLERANCE[character["overtaking"]]
    close = [p for p in by_expected if sim.expected[p.name] - reference <= tolerance]
    chosen = min(close, key=lambda p: (p.stops, sim.expected[p.name]))
    if chosen is by_expected[0]:
        why_a = "The quickest plan in our simulation, safety cars included."
    else:
        why_a = ("{:.1f}s slower than the quickest plan ({}) in our simulation, but a stop "
                 "fewer: where overtaking is {}, the places a stop costs are worth "
                 "more.").format(sim.expected[chosen.name] - reference,
                                 by_expected[0].name, character["overtaking"])

    tyre = start_tyre(Oriented.of(chosen, laps), driver.grid if driver else None,
                      character, inputs)
    plan_a = Oriented.of(chosen, laps, hardest_first=tyre["hardest_first"])
    plans = [_describe("A", plan_a, laps, sim, reference, why_a)]

    other = next((p for p in by_expected if p.stops != chosen.stops), None)
    if other is not None:
        plans.append(_describe(
            "B", Oriented.of(other, laps, hardest_first=tyre["hardest_first"]),
            laps, sim, reference,
            "The best {}-stop: switch to it if the tyres wear {} than modelled.".format(
                other.stops, "faster" if other.stops > chosen.stops else "slower")))

    plan_c = Oriented.of(chosen, laps, hardest_first=not tyre["hardest_first"])
    if plan_c.sequence != plan_a.sequence:
        longer = HARDNESS[plan_c.sequence[0]] > HARDNESS[plan_a.sequence[0]]
        plans.append(_describe(
            "C", plan_c, laps, sim, reference,
            "Plan A's tyres the other way round: the same race time in our model, with a "
            "{} first stint. {}".format(
                "long" if longer else "short",
                "Use it to run long for a safety car or past traffic."
                if longer else "Use it if the start goes well and there is clear air to use.")))

    result: Dict[str, Any] = {
        "race_laps": laps,
        "pit_loss_seconds": round(inputs.pit_loss, 1),
        "race_character": character,
        "plans": plans,
        "plans_guide_query": "fastest race single car one stop two stop combination",
        "targets_guide_query": "plan A target stop lap easy to remember",
        "start_tyre": tyre,
        "safety_car": safety_car_window(inputs, plan_a),
        "inputs": inputs.sources,
        "not_modelled": NOT_MODELLED,
    }
    if driver is not None:
        result["driver"] = {"name": driver.driver, "team": driver.team, "grid": driver.grid}
        traffic = in_traffic(field, driver, plan_a, inputs)
        result["pit_stop"] = traffic
        result["undercut"] = undercut(field, driver, plan_a, inputs)
        result["edge_cases"] = edge_cases(traffic, plan_a, inputs)
    return result


# ── Rendering ────────────────────────────────────────────────────────────────

ENGINE_KEY = "strategy engine"


def _plan_line(plan: Dict[str, Any]) -> str:
    stints = ", ".join("{} laps {}-{}".format(s["compound"].title(), s["from_lap"], s["to_lap"])
                       for s in plan["stints"])
    stops = "; ".join(
        "stop {}: target lap {} (optimum {}, window {}-{})".format(
            i + 1, s["target_lap"], s["optimum_lap"], s["window"][0], s["window"][1])
        for i, s in enumerate(plan["pit_stops"]))
    delta = ("the reference" if plan["expected_delta_seconds"] == 0
             else "{:+.1f}s".format(plan["expected_delta_seconds"]))
    return "Plan {} {} ({}-stop, {} on average): {}. {}. {}".format(
        plan["label"], plan["name"], plan["stops"], delta, stints, stops, plan["why"])


def engine_facts(result: Dict[str, Any]) -> Dict[str, Any]:
    """The engine's plan as facts for Bernie, one line per call."""
    if not result:
        return {}
    who = result.get("driver")
    lines = [result["race_character"]["summary"]]
    lines += [_plan_line(p) for p in result["plans"]]
    lines.append("Start tyre: {}. {}".format(result["start_tyre"]["compound"].title(),
                                             result["start_tyre"]["why"]))
    lines += ["Plan S (safety car): " + line for line in result["safety_car"]["summary"]]
    for section in ("pit_stop", "undercut", "edge_cases"):
        lines += (result.get(section) or {}).get("summary") or []
    key = ("{}: plan for {} from P{}".format(ENGINE_KEY, who["name"], who["grid"]) if who
           else "{}: plan for the race (no car named)".format(ENGINE_KEY))
    return {
        key: lines,
        "{}: how it plans".format(ENGINE_KEY): (
            "Built the way the strategy guide builds a race plan: the quickest race for a "
            "car alone, then adjusted for how hard it is to pass, the start, the safety "
            "car and the cars around. Target laps are round numbers inside each window. "
            "Not modelled: " + result["not_modelled"]),
    }


def excerpt(text: str, query: str, limit: int = 420) -> str:
    """The sentences of a passage that best answer a query, about ``limit`` long.

    A passage is a paragraph; the reader needs the two sentences in it that make
    the point, starting at the one sharing most words with the question.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    if not sentences:
        return ""
    words = set(re.findall(r"[a-z]{3,}", (query or "").lower()))
    scores = [len(words & set(re.findall(r"[a-z]{3,}", s.lower()))) for s in sentences]
    start = max(range(len(sentences)), key=lambda i: (scores[i], -i))
    chosen: List[str] = []
    for sentence in sentences[start:]:
        if chosen and len(" ".join(chosen)) + len(sentence) > limit:
            break
        chosen.append(sentence)
    out = " ".join(chosen)
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "…"
    return out
