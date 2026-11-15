"""Race strategy simulation: what Bernie plans from.

Asked "what strategy should they run?", a strategist does not answer that they
have not been told one. They take what they know about the circuit — race
distance, what a stop costs, how each compound falls away, how often the safety
car comes out — and work out which plans are quickest and how robust each is.
This module does that arithmetic, so that Bernie's plan is a computed result she
can cite rather than a guess dressed as one.

**Deterministic and seeded.** The same inputs give the same plans, so a reader
who asks twice is not handed two different "best" strategies.

**Every input is labelled with where it came from.** Measured figures carry
their sample; where the corpus has nothing, a generic figure is used and the
facts say so. A plan built on a generic pit loss is still a plan — that is what
a strategist does at a new circuit — but the reader is told which numbers are
this circuit's and which are not.

The model is deliberately simple: linear tyre wear per compound with a cliff
past the longest stint anyone has run here, a fixed pit loss, and
neutralisations on random laps. Traffic, track position, tyre allocation and
weather are not modelled, and the facts say that too.
"""

import random
from dataclasses import dataclass, field
from itertools import combinations_with_replacement
from typing import Any, Dict, List, Optional, Sequence, Tuple

COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
SHORT = {"SOFT": "S", "MEDIUM": "M", "HARD": "H"}

#: Fallbacks when the corpus holds nothing for a circuit. Typical modern
#: dry-race figures, used only to keep a plan possible and always labelled.
GENERIC_RACE_LAPS = 57
GENERIC_PIT_LOSS = 22.0
GENERIC_TYRES = {
    "SOFT": {"offset": -0.5, "degradation": 0.10, "life": 20},
    "MEDIUM": {"offset": 0.0, "degradation": 0.06, "life": 30},
    "HARD": {"offset": 0.4, "degradation": 0.04, "life": 42},
}
GENERIC_SAFETY_CAR_RATE = 0.4
GENERIC_VSC_RATE = 0.3

#: A measured slope below this is track evolution or noise, not a tyre that
#: does not wear. Clamped so a hard never looks free to run for ever.
MIN_DEGRADATION = 0.01

#: Extra seconds per lap, growing each lap, once a tyre is past its life. Steep
#: on purpose: past the longest stint anyone has run here, the corpus has no
#: evidence the tyre survives at all.
CLIFF_SECONDS_PER_LAP = 0.35

#: The shortest stint a plan may contain.
MIN_STINT_LAPS = 5

#: A stop's window is every lap within this many seconds of the best lap for it.
WINDOW_SECONDS = 2.0

#: Pit loss under each neutralisation, as a share of the green-flag loss. The
#: field is slowed, so the time lost in the lane relative to it shrinks.
SAFETY_CAR_LOSS_SHARE = 0.5
VSC_LOSS_SHARE = 0.7

#: Out-lap cost of cold tyres, for the undercut arithmetic.
OUT_LAP_WARMUP_SECONDS = 1.0

SIMULATIONS = 2000
SEED = 20261011


@dataclass
class Tyre:
    offset: float
    degradation: float
    life: int
    source: str
    #: cumulative[n] is the cost of an n-lap stint. Filled by ``build_inputs``.
    cumulative: List[float] = field(default_factory=list)

    def lap_cost(self, age: int) -> float:
        over = max(0, age - self.life + 1)
        return self.offset + self.degradation * age + CLIFF_SECONDS_PER_LAP * over

    def prepare(self, laps: int) -> None:
        total, self.cumulative = 0.0, [0.0]
        for age in range(laps):
            total += self.lap_cost(age)
            self.cumulative.append(total)


@dataclass
class Inputs:
    race_laps: int
    pit_loss: float
    tyres: Dict[str, Tyre]
    safety_car_rate: float
    vsc_rate: float
    #: Where each input came from, one line each.
    sources: List[str] = field(default_factory=list)


@dataclass
class Plan:
    sequence: Tuple[str, ...]
    pit_laps: List[int]
    windows: List[Tuple[int, int]]
    green_time: float

    @property
    def stops(self) -> int:
        return len(self.pit_laps)

    @property
    def name(self) -> str:
        return "-".join(SHORT[c] for c in self.sequence)

    def describe(self) -> str:
        stops = "; ".join(
            "stop {} lap {} (window laps {}-{})".format(i + 1, lap, lo, hi)
            for i, (lap, (lo, hi)) in enumerate(zip(self.pit_laps, self.windows))
        )
        return "{}-stop {} [{}]: {}".format(
            self.stops, " > ".join(c.title() for c in self.sequence), self.name, stops
        )


def build_inputs(
    profile: Optional[Dict[str, Any]], scheduled_laps: Optional[int] = None
) -> Inputs:
    """Turn a measured circuit profile into simulator inputs, filling gaps generically."""
    profile = profile or {}
    sources: List[str] = []
    sample = profile.get("races_in_sample") or 0
    where = "the last {} race{} here ({})".format(
        sample, "" if sample == 1 else "s",
        ", ".join(str(s) for s in profile.get("seasons") or []),
    )

    if scheduled_laps:
        race_laps = int(scheduled_laps)
        sources.append("race distance {} laps (official scheduled distance)".format(race_laps))
    elif profile.get("race_laps"):
        race_laps = int(profile["race_laps"])
        sources.append("race distance {} laps (measured over {})".format(race_laps, where))
    else:
        race_laps = GENERIC_RACE_LAPS
        sources.append(
            "race distance {} laps (GENERIC assumption: no race distance held for "
            "this circuit)".format(race_laps)
        )

    if profile.get("pit_loss_seconds"):
        pit_loss = float(profile["pit_loss_seconds"])
        sources.append(
            "a pit stop costs {:.1f}s against a racing lap (measured over {} stops "
            "in {})".format(pit_loss, profile.get("pit_loss_sample", "?"), where)
        )
    else:
        pit_loss = GENERIC_PIT_LOSS
        sources.append(
            "a pit stop costs {:.0f}s (GENERIC assumption: no stops measured "
            "here)".format(pit_loss)
        )

    measured = profile.get("compounds") or {}
    tyres: Dict[str, Tyre] = {}
    for compound in COMPOUNDS:
        generic = GENERIC_TYRES[compound]
        entry = measured.get(compound) or {}
        parts = []

        degradation = entry.get("degradation_seconds_per_lap")
        if degradation is not None:
            degradation = max(MIN_DEGRADATION, float(degradation))
            parts.append("wears {:.3f}s/lap (measured over {} stints)".format(
                degradation, entry.get("degradation_sample", "?")))
        else:
            degradation = generic["degradation"]
            parts.append("wears {:.3f}s/lap (GENERIC)".format(degradation))

        if compound == "MEDIUM":
            offset = 0.0
            parts.append("the pace reference")
        elif entry.get("pace_offset_seconds") is not None:
            offset = float(entry["pace_offset_seconds"])
            parts.append("{:+.2f}s/lap against the medium when new (measured)".format(offset))
        else:
            offset = generic["offset"]
            parts.append("{:+.2f}s/lap against the medium when new (GENERIC)".format(offset))

        if entry.get("longest_stint_laps"):
            life = int(entry["longest_stint_laps"])
            parts.append("longest stint run here {} laps".format(life))
        else:
            life = generic["life"]
            parts.append("usable for about {} laps (GENERIC)".format(life))

        tyre = Tyre(offset, degradation, max(MIN_STINT_LAPS + 1, min(life, race_laps)),
                    "; ".join(parts))
        tyre.prepare(race_laps)
        tyres[compound] = tyre
        sources.append("{}: {}".format(compound.title(), tyre.source))

    races = profile.get("race_control_races") or 0
    if races:
        # Smoothed towards the generic rate, so two calm visits cannot make a
        # circuit immune and two chaotic ones cannot make a safety car certain.
        sc_seen = profile.get("safety_car_races", 0)
        vsc_seen = profile.get("virtual_safety_car_races", 0)
        safety_car_rate = (sc_seen + GENERIC_SAFETY_CAR_RATE * 2) / (races + 2)
        vsc_rate = (vsc_seen + GENERIC_VSC_RATE * 2) / (races + 2)
        sources.append(
            "safety car in {} of the last {} races here and a virtual safety car in "
            "{} (simulated as {:.0%} and {:.0%} a race)".format(
                sc_seen, races, vsc_seen, safety_car_rate, vsc_rate)
        )
    else:
        safety_car_rate, vsc_rate = GENERIC_SAFETY_CAR_RATE, GENERIC_VSC_RATE
        sources.append(
            "safety car {:.0%} and virtual safety car {:.0%} a race (GENERIC "
            "assumption: no race control history held here)".format(
                safety_car_rate, vsc_rate)
        )

    if profile.get("weather_races"):
        sources.append("rain fell in {} of the last {} races here; the simulation "
                       "plans a dry race".format(profile.get("wet_races", 0),
                                                 profile["weather_races"]))
    for key, label in (("winner_stop_counts", "winners"),
                       ("top_ten_stop_counts", "top-ten finishers")):
        if profile.get(key):
            sources.append("stops made by {} in {}: {}".format(
                label, where,
                ", ".join("{} stop{} x{}".format(k, "" if k == "1" else "s", v)
                          for k, v in profile[key].items())))

    return Inputs(race_laps, pit_loss, tyres, safety_car_rate, vsc_rate, sources)


def _sequences(max_stops: int) -> List[Tuple[str, ...]]:
    """Every combination of compounds over two to ``max_stops + 1`` stints.

    A dry race must use at least two different compounds, so single-compound
    sets are excluded.

    One order per combination, softest first. Wear here depends on a tyre's age
    and not on when in the race it runs, so Soft > Medium and Medium > Soft
    cost exactly the same; listing both put pairs of identical plans in front
    of Bernie, each "+0.0s" behind the other, which reads as a ranking the
    model never made. The facts say the order is interchangeable instead.
    """
    return [
        seq
        for stints in range(2, max_stops + 2)
        for seq in combinations_with_replacement(COMPOUNDS, stints)
        if len(set(seq)) >= 2
    ]


def _optimise(sequence: Sequence[str], inputs: Inputs) -> Optional[Plan]:
    """Best pit laps for one compound order, with each stop's window.

    Prefix and suffix tables over (stint, laps covered): the best total with a
    stop forced on any given lap is then the sum of two lookups, which is what
    gives each stop a window as well as an optimum.
    """
    n, k, inf = inputs.race_laps, len(sequence), float("inf")
    costs = [inputs.tyres[c].cumulative for c in sequence]

    # prefix[i][l]: best cost of the first i stints covering laps 1..l.
    prefix = [[inf] * (n + 1) for _ in range(k + 1)]
    choice = [[0] * (n + 1) for _ in range(k + 1)]
    prefix[0][0] = 0.0
    for i in range(1, k + 1):
        cost, before = costs[i - 1], prefix[i - 1]
        for l in range(MIN_STINT_LAPS * i, n + 1):
            best, arg = inf, 0
            for prev in range(MIN_STINT_LAPS * (i - 1), l - MIN_STINT_LAPS + 1):
                total = before[prev] + cost[l - prev]
                if total < best:
                    best, arg = total, prev
            prefix[i][l], choice[i][l] = best, arg
    if prefix[k][n] == inf:
        return None

    # suffix[i][l]: best cost of stints i..k-1 covering laps l+1..n.
    suffix = [[inf] * (n + 1) for _ in range(k + 1)]
    suffix[k][n] = 0.0
    for i in range(k - 1, -1, -1):
        cost, after = costs[i], suffix[i + 1]
        for l in range(n - MIN_STINT_LAPS, -1, -1):
            suffix[i][l] = min(
                (cost[end - l] + after[end] for end in range(l + MIN_STINT_LAPS, n + 1)),
                default=inf,
            )

    pit_laps: List[int] = []
    l = n
    for i in range(k, 1, -1):
        l = choice[i][l]
        pit_laps.append(l)
    pit_laps.reverse()

    optimum = prefix[k][n]
    windows = []
    for stop in range(1, k):
        ok = [
            lap for lap in range(1, n)
            if prefix[stop][lap] + suffix[stop][lap] <= optimum + WINDOW_SECONDS
        ]
        windows.append((min(ok), max(ok)))

    return Plan(tuple(sequence), pit_laps, windows, optimum + inputs.pit_loss * (k - 1))


def _plan_time(
    plan: Plan, inputs: Inputs, pit_laps: Sequence[int], losses: Sequence[float]
) -> float:
    total, start = 0.0, 0
    for compound, end in zip(plan.sequence, list(pit_laps) + [inputs.race_laps]):
        total += inputs.tyres[compound].cumulative[end - start]
        start = end
    return total + sum(losses)


def _react(plan: Plan, inputs: Inputs, lap: int, share: float) -> float:
    """Race time for a plan when a neutralisation falls on ``lap``.

    The team either keeps to the plan or moves its next stop to the neutralised
    lap, whichever is quicker — the call every pit wall makes when the safety
    car comes out. Stops already made are not revisited.
    """
    full = [inputs.pit_loss] * plan.stops
    best = plan.green_time
    previous = 0
    for j, planned in enumerate(plan.pit_laps):
        if planned < lap:
            previous = planned
            continue
        following = plan.pit_laps[j + 1] if j + 1 < plan.stops else inputs.race_laps
        if lap - previous >= MIN_STINT_LAPS and following - lap >= MIN_STINT_LAPS:
            moved = list(plan.pit_laps)
            moved[j] = lap
            losses = list(full)
            losses[j] = inputs.pit_loss * share
            best = min(best, _plan_time(plan, inputs, moved, losses))
        break
    return best


@dataclass
class Simulation:
    inputs: Inputs
    plans: List[Plan]
    #: Mean race time over the simulated races, by plan name.
    expected: Dict[str, float]
    #: Share of simulated races in which each plan was quickest.
    wins: Dict[str, float]
    #: Share of simulated races neutralised at least once.
    neutralised_share: float
    undercut: Optional[Tuple[str, str, int, float]]


def simulate(inputs: Inputs, shortlist: int = 5) -> Simulation:
    """Rank plans for a green-flag race, then test them against neutralisations."""
    max_stops = 3 if inputs.race_laps >= 40 else 2
    plans = [p for p in (_optimise(s, inputs) for s in _sequences(max_stops)) if p]
    plans.sort(key=lambda p: p.green_time)

    # The quickest few, plus the quickest of each stop count, so "what about a
    # two-stop?" has an answer even when the two-stop ranks low.
    chosen = plans[:shortlist]
    for stops in sorted({p.stops for p in plans}):
        best = next(p for p in plans if p.stops == stops)
        if best not in chosen:
            chosen.append(best)

    rng = random.Random(SEED)
    totals = {p.name: 0.0 for p in chosen}
    wins = {p.name: 0 for p in chosen}
    neutralised = 0
    for _ in range(SIMULATIONS):
        events = []
        if rng.random() < inputs.safety_car_rate:
            events.append((rng.randint(2, inputs.race_laps - 3), SAFETY_CAR_LOSS_SHARE))
        if rng.random() < inputs.vsc_rate:
            events.append((rng.randint(2, inputs.race_laps - 3), VSC_LOSS_SHARE))
        neutralised += bool(events)
        results = {}
        for plan in chosen:
            # Each event is assessed on its own and the better reaction kept:
            # two neutralisations rarely both fall inside one stop's window,
            # and this cannot double-count a discount.
            results[plan.name] = min(
                [plan.green_time] + [_react(plan, inputs, lap, share) for lap, share in events]
            )
            totals[plan.name] += results[plan.name]
        wins[min(results, key=results.get)] += 1

    return Simulation(
        inputs=inputs,
        plans=chosen,
        expected={k: v / SIMULATIONS for k, v in totals.items()},
        wins={k: v / SIMULATIONS for k, v in wins.items()},
        neutralised_share=neutralised / SIMULATIONS,
        undercut=_undercut(chosen[0], inputs) if chosen else None,
    )


def _undercut(plan: Plan, inputs: Inputs) -> Tuple[str, str, int, float]:
    """What pitting a lap before a rival is worth at the leading plan's first stop.

    The car that stops first runs new tyres against worn ones for a lap. The
    gain is the worn tyre's lost time less the new tyre's offset and its cold
    out-lap.
    """
    lap = plan.pit_laps[0]
    old, new = plan.sequence[0], plan.sequence[1]
    worn = inputs.tyres[old].lap_cost(lap - 1)
    fresh = inputs.tyres[new].lap_cost(0) + OUT_LAP_WARMUP_SECONDS
    return old, new, lap, worn - fresh


#: Where the simulation sits in the facts pack. Named so nobody mistakes it for
#: a forecast of what teams will do.
INPUTS_KEY = "strategy simulation inputs"
GREEN_KEY = "strategy simulation: plans ranked for a dry race with no safety car"
NEUTRALISED_KEY = "strategy simulation: plans ranked across simulated races with safety cars"


def strategy_facts(sim: Simulation) -> Dict[str, Any]:
    """Render a simulation as facts for Bernie."""
    if not sim.plans:
        return {}
    ranked = sorted(sim.plans, key=lambda p: p.green_time)
    best = ranked[0]
    by_expected = sorted(sim.plans, key=lambda p: sim.expected[p.name])
    baseline = sim.expected[by_expected[0].name]

    facts: Dict[str, Any] = {
        INPUTS_KEY: sim.inputs.sources,
        GREEN_KEY: [
            "{} — {}".format(
                plan.describe(),
                "quickest" if plan is best
                else "{:+.1f}s slower".format(plan.green_time - best.green_time),
            )
            for plan in ranked
        ],
        NEUTRALISED_KEY: [
            "{} ({}-stop): {} on average, quickest in {:.0%} of {} simulated races".format(
                plan.name, plan.stops,
                "the reference" if i == 0
                else "{:+.1f}s".format(sim.expected[plan.name] - baseline),
                sim.wins[plan.name], SIMULATIONS,
            )
            for i, plan in enumerate(by_expected)
        ] + [
            "{:.0%} of simulated races had a safety car or virtual safety car; when "
            "one fell near a planned stop the team pitted under it".format(
                sim.neutralised_share)
        ],
    }
    if sim.undercut:
        old, new, lap, gain = sim.undercut
        facts["strategy simulation: undercut"] = (
            "Around lap {}, a car pitting from {} to {} one lap before a rival gains "
            "about {:.1f}s on that lap{}".format(
                lap, old.lower(), new.lower(), gain,
                "." if gain > 0 else ", so it loses time: the overcut is stronger here.",
            )
        )
    facts["strategy simulation: not modelled"] = (
        "Traffic and track position, gaps to rivals, each team's tyre allocation, "
        "weather, and car- or driver-specific tyre wear. The order of stints is "
        "interchangeable in this model (plans are listed softest first), so which "
        "compound to start on is a call about launch grip and track position, not "
        "something it ranks. Times are relative race times for one car on a clear "
        "track."
    )
    return facts
