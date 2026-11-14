"""Where a race stands at the end of a lap, read from its timing data.

The live forecast restarts the lap simulator from the real race rather than
from the grid. This module turns timing rows (laps, pit stops, race-control
messages and final statuses) into that starting point, as it stood when the
leader finished lap ``upto``. The same function serves two callers:

* **the replay backtest** passes a finished race's rows and walks ``upto``
  from 1 to the flag, so the forecast at lap 20 is made from what was known
  at lap 20;
* **the live feed** passes the rows received so far and the latest lap.

**Nothing from after ``upto`` is read**, with one exception that live timing
has too: whether a car that has stopped appearing is out or only lapped. Live,
the timing screen says so; in a replay it is the car's final status.

**Gaps.** Our lap rows carry lap times but no positions, so the order on the
road is rebuilt by adding lap times up. A missing lap time (a timing gap, a
lap OpenF1 dropped) is filled with that lap's median across the field, so a gap
in the data never moves a car. A car behind the leader's lap count is measured
at the last lap it completed, plus a typical lap for each lap it is down.
Live rows also carry the timing screen's own gap to the leader at the line;
where one is there it is used instead of the sum. Summed live, the gaps
behind P2 came out out of order and were all pushed to ``MIN_GAP`` apart
(São Paulo 2026: Verstappen +8.4 s at lap 50 when the screen said +17.6 s).

**Pace this race.** For every clean lap (not lap 1, not an in- or out-lap, not
under a safety car, VSC or red flag, not an outlier) the lap time is corrected
for fuel and for the compound and age of the tyre with the circuit profile's
own figures, then compared with the median car on the same lap. That removes
track evolution and whatever the profile has wrong about the circuit today.
Each car's mean of those comparisons, and how many laps it rests on, is what
``live_forecast.posterior_pace`` blends with the pre-race pace.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services.ingestion_client import PitStop, RaceControlMessage, RaceLap, RaceResult
from app.services.lap_simulation import DRY, SAFETY_CAR_LAPS, VSC_LAPS

#: A lap whose field median is this much slower than the typical lap was
#: neutralised, whatever the messages say. As in ``race_pace``.
NEUTRALISED_FACTOR = 1.05
#: A lap this much slower than the field's typical lap is not pace.
OUTLIER_FACTOR = 1.07
#: One lap's corrected deviation is capped here, seconds: a lock-up or a
#: moment of traffic should not count as a second of pace.
MAX_LAP_DEVIATION = 3.0
#: Fewer clean cars than this on a lap and its median says little.
MIN_CARS_PER_LAP = 3

WET_COMPOUNDS = ("INTERMEDIATE", "WET")


class RaceState:
    """The race at the end of the leader's lap ``lap``. Per-driver arrays
    follow ``drivers``."""

    def __init__(self, drivers: Sequence[str], lap: int, total_laps: int) -> None:
        n = len(drivers)
        self.drivers = list(drivers)
        self.lap = lap
        self.total_laps = total_laps
        #: Seconds behind the leader on the road.
        self.gaps = np.zeros(n)
        self.retired = np.zeros(n, dtype=bool)
        self.retired_lap = np.full(n, total_laps + 1, dtype=int)
        #: Compound name per car, upper case; "" when unknown.
        self.compound: List[str] = [""] * n
        self.tyre_age = np.zeros(n)
        self.stops = np.zeros(n, dtype=int)
        self.used = np.zeros((n, len(DRY)), dtype=bool)
        self.safety_car_laps = 0
        self.vsc_laps = 0
        #: Mean corrected deviation from the median car, seconds per lap
        #: (negative = quicker), and how many clean laps it rests on.
        self.observed_pace = np.zeros(n)
        self.observed_laps = np.zeros(n, dtype=int)

    @property
    def wet(self) -> bool:
        return any(c in WET_COMPOUNDS for c, out in zip(self.compound, self.retired) if not out)

    def running_order(self) -> List[str]:
        """Cars on the road first, by gap; retired cars after, latest out first."""
        key = np.where(self.retired, 1e12 - self.retired_lap, self.gaps)
        return [self.drivers[i] for i in np.argsort(key, kind="stable")]

    def compound_index(self) -> np.ndarray:
        return np.array([DRY.index(c) if c in DRY else -1 for c in self.compound], dtype=int)


def _finishing_status(result: Optional[RaceResult]) -> bool:
    """Took the flag (possibly laps down), as opposed to stopping."""
    if result is None:
        return False
    status = (result.status or "").strip().lower()
    return status == "finished" or status.startswith("+") or "lap" in status


def neutralisation(messages: Sequence[RaceControlMessage], upto: int) -> Tuple[set, int, int]:
    """Laps run under a safety car, VSC or red flag up to ``upto``, and how
    many more laps the safety car or VSC out at the end of ``upto`` is expected
    to stay out."""
    rows = sorted((m for m in messages if m.lap is not None and m.lap <= upto),
                  key=lambda m: m.lap)
    neutral = set()
    sc_from = vsc_from = None
    for message in rows:
        text = (message.message or "").upper()
        if message.category == "SafetyCar":
            if text.startswith("VIRTUAL SAFETY CAR DEPLOYED"):
                vsc_from = message.lap
            elif text.startswith("VIRTUAL SAFETY CAR ENDING"):
                if vsc_from is not None:
                    neutral.update(range(vsc_from, message.lap + 1))
                vsc_from = None
            elif text.startswith("SAFETY CAR DEPLOYED"):
                sc_from = message.lap
            elif text.startswith("SAFETY CAR IN THIS LAP"):
                if sc_from is not None:
                    neutral.update(range(sc_from, message.lap + 1))
                sc_from = None
        elif message.category == "Flag" and text.startswith("RED FLAG"):
            # The race restarts behind the safety car: it ends as one does.
            sc_from = message.lap
        elif sc_from is not None and message.lap > sc_from and (
                text.startswith("ROLLING START") or text.startswith("DRS ENABLED")):
            # Some restarts never get an "IN THIS LAP" message (Spa 2025
            # started behind the safety car after a red flag and only says
            # ROLLING START). DRS is never enabled under a safety car.
            neutral.update(range(sc_from, message.lap))
            sc_from = None
    sc_left = vsc_left = 0
    if sc_from is not None:
        neutral.update(range(sc_from, upto + 1))
        sc_left = max(SAFETY_CAR_LAPS - (upto - sc_from), 1)
    if vsc_from is not None:
        neutral.update(range(vsc_from, upto + 1))
        vsc_left = max(VSC_LAPS - (upto - vsc_from), 1)
    return neutral, sc_left, vsc_left


def race_state(
    drivers: Sequence[str],
    laps: Sequence[RaceLap],
    pit_stops: Sequence[PitStop],
    messages: Sequence[RaceControlMessage],
    results: Sequence[RaceResult],
    upto: int,
    total_laps: int,
    profile: Dict,
) -> RaceState:
    """The race at the end of the leader's lap ``upto`` (see the module notes).

    ``results`` is only read for a car's final status, to tell a car that has
    stopped from one that is laps down.
    """
    state = RaceState(drivers, upto, total_laps)
    index = {d: i for i, d in enumerate(drivers)}
    by_driver: Dict[str, Dict[int, RaceLap]] = defaultdict(dict)
    last_overall: Dict[str, int] = defaultdict(int)
    for row in laps:
        if row.driver not in index or row.lap < 1:
            continue
        last_overall[row.driver] = max(last_overall[row.driver], row.lap)
        if row.lap <= upto:
            by_driver[row.driver][row.lap] = row
    status = {r.driver: r for r in results}

    # The field's median lap, per lap, from cars not in or out of the pits:
    # the stand-in for a missing time.
    pit_laps = _pit_laps(by_driver, pit_stops, upto)
    times: Dict[int, List[float]] = defaultdict(list)
    for driver, rows in by_driver.items():
        for number, row in rows.items():
            if row.lap_time_seconds > 0 and (driver, number) not in pit_laps:
                times[number].append(row.lap_time_seconds)
    medians = {number: float(np.median(v)) for number, v in times.items()}
    typical = (float(np.median(list(medians.values()))) if medians
               else float(profile["typical_lap"]))

    # Elapsed time at the end of each lap, per car.
    half_stop = float(profile.get("pit_loss", 0.0)) / 2.0
    elapsed: Dict[str, Dict[int, float]] = {}
    completed: Dict[str, int] = {}
    for driver in drivers:
        rows = by_driver.get(driver, {})
        done = max(rows, default=0)
        completed[driver] = done
        total, series = 0.0, {}
        for number in range(1, done + 1):
            row = rows.get(number)
            seconds = row.lap_time_seconds if row is not None else 0.0
            if seconds <= 0:
                # A missing time: the field's lap, plus half a stop if the car
                # came in or went out on it, so a lost in-lap does not hand a
                # car the time it spent in the pit lane.
                seconds = medians.get(number, typical)
                if (driver, number) in pit_laps:
                    seconds += half_stop
            total += seconds
            series[number] = total
        elapsed[driver] = series

    # The leader's time at each lap: the fastest elapsed time among cars there.
    leader_at: Dict[int, float] = {}
    for series in elapsed.values():
        for number, value in series.items():
            leader_at[number] = min(leader_at.get(number, np.inf), value)

    for driver in drivers:
        i = index[driver]
        done = completed[driver]
        rows = by_driver.get(driver, {})
        stopped = done < upto and last_overall[driver] < upto
        if stopped and not _finishing_status(status.get(driver)):
            state.retired[i] = True
            state.retired_lap[i] = max(done, 1)
        if done == 0:
            state.gaps[i] = upto * typical  # never started a lap
        else:
            measured = rows[done].gap_to_leader if done in rows else None
            behind = (measured if measured is not None and measured >= 0
                      else elapsed[driver][done] - leader_at[done])
            state.gaps[i] = behind + (upto - done) * typical

        if rows:
            current = rows[done]
            compound = (current.compound or "").upper()
            state.compound[i] = compound if compound in DRY + WET_COMPOUNDS else ""
            for r in rows.values():
                c = (r.compound or "").upper()
                if c in DRY:
                    state.used[i, DRY.index(c)] = True
        if rows and rows[done].stint and rows[done].stint > 0:
            state.stops[i], state.tyre_age[i] = _stops_from_stints(rows, done)
        else:
            # No stint numbers: count the stops the pit lane recorded.
            stops = sorted(s.lap for s in pit_stops if s.driver == driver and 0 < s.lap <= upto)
            state.stops[i] = len(stops)
            state.tyre_age[i] = max(min(done, upto) - (stops[-1] if stops else 0), 0)

    _order_by_positions(state, by_driver, completed)

    neutral, state.safety_car_laps, state.vsc_laps = neutralisation(messages, upto)
    neutral |= {n for n, m in medians.items() if m > NEUTRALISED_FACTOR * typical}
    _observe_pace(state, by_driver, neutral, typical, profile)
    return state


#: The gap, seconds, a car is put behind the car ahead when the timing
#: feed's positions overrule the order the summed lap times give.
MIN_GAP = 0.3


def _order_by_positions(state: RaceState, by_driver, completed: Dict[str, int]) -> None:
    """Make the gaps agree with the timing feed's order on the road.

    Summed lap times are the gaps, but they are only as good as every lap's
    time: a deleted lap filled with the field's median, then a lapped car
    catching the queue behind a safety car, can put a car a minute ahead of
    cars it never passed (Piastri, Australia 2025). When the lap rows carry
    positions, the running cars are ordered by laps done, then by their place
    at the end of their last lap, and a car whose gap puts it level with or
    ahead of the car in front is moved to just behind it. A gap already in
    order stays as measured, however close (São Paulo 2026: Norris 0.17 s
    behind Lawson at lap 5 came out 0.3 s). Without positions nothing changes.
    """
    running = [i for i, d in enumerate(state.drivers) if not state.retired[i]]
    place = {}
    for i in running:
        driver = state.drivers[i]
        row = by_driver.get(driver, {}).get(completed[driver])
        if row is not None and row.position > 0:
            place[i] = row.position
    if len(place) < max(2, len(running) // 2):
        return
    order = sorted(running, key=lambda i: (-completed[state.drivers[i]],
                                           place.get(i, 999), state.gaps[i]))
    previous = None
    for i in order:
        if previous is None:
            state.gaps[i] = max(state.gaps[i], 0.0)
        elif state.gaps[i] <= previous:
            state.gaps[i] = previous + MIN_GAP
        previous = state.gaps[i]
    leader = state.gaps[order[0]]
    for i in running:
        state.gaps[i] -= leader


#: A change of stint number this early without a change of compound is a
#: timing artefact, not a stop (Australia 2025 has three for every car on
#: laps 2-4, all on intermediates).
EARLY_STINT_LAPS = 5


def _stops_from_stints(rows: Dict[int, RaceLap], done: int) -> Tuple[int, int]:
    """(stops made, laps on the current tyre) from the stint numbers on a
    car's laps up to ``done``, ignoring early same-compound stint changes."""
    stops, fitted, previous = 0, min(rows), None
    for number in sorted(rows):
        row = rows[number]
        if previous is not None and row.stint and previous.stint and row.stint != previous.stint:
            same = (row.compound or "").upper() == (previous.compound or "").upper()
            if not (same and number <= EARLY_STINT_LAPS):
                stops += 1
                fitted = number
        previous = row
    return stops, done - fitted + 1


def _pit_laps(by_driver, pit_stops: Sequence[PitStop], upto: int) -> set:
    """(driver, lap) for every in-lap and out-lap up to ``upto``: from the
    stint numbers on the lap rows, and from the pit lane's own records."""
    out = set()
    for driver, rows in by_driver.items():
        for number, row in rows.items():
            after = rows.get(number + 1)
            if after is not None and row.stint and after.stint and after.stint != row.stint:
                out.add((driver, number))
                out.add((driver, number + 1))
    for stop in pit_stops:
        if 0 < stop.lap <= upto:
            out.add((stop.driver, stop.lap))
            out.add((stop.driver, stop.lap + 1))
    return out


def _observe_pace(state: RaceState, by_driver, neutral: set, typical: float,
                  profile: Dict) -> None:
    """Each car's corrected pace against the median car, over its clean laps."""
    tyres = profile["tyres"]
    fuel = profile["fuel_per_lap"]
    deviations: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    for i, driver in enumerate(state.drivers):
        rows = by_driver.get(driver, {})
        if not rows:
            continue
        stint_start: Dict[int, int] = {}
        stint_end: Dict[int, int] = {}
        for number, row in rows.items():
            stint_start[row.stint] = min(stint_start.get(row.stint, number), number)
            stint_end[row.stint] = max(stint_end.get(row.stint, number), number)
        first_stint, last_stint = min(stint_start), max(stint_start)
        for number, row in rows.items():
            compound = (row.compound or "").upper()
            in_or_out = ((row.stint != first_stint and number == stint_start[row.stint])
                         or (row.stint != last_stint and number == stint_end[row.stint]))
            if (number < 2 or row.lap_time_seconds <= 0 or number in neutral
                    or compound not in DRY or in_or_out
                    or row.lap_time_seconds > OUTLIER_FACTOR * typical):
                continue
            age = number - stint_start[row.stint] + 1
            corrected = (row.lap_time_seconds - fuel * number
                         - tyres[compound]["offset"] - tyres[compound]["wear"] * age)
            deviations[number].append((i, corrected))
    totals = np.zeros(len(state.drivers))
    for entries in deviations.values():
        if len(entries) < MIN_CARS_PER_LAP:
            continue
        median = float(np.median([c for _, c in entries]))
        for i, corrected in entries:
            totals[i] += float(np.clip(corrected - median, -MAX_LAP_DEVIATION, MAX_LAP_DEVIATION))
            state.observed_laps[i] += 1
    counted = state.observed_laps > 0
    state.observed_pace[counted] = totals[counted] / state.observed_laps[counted]
