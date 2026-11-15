"""How races at a circuit have actually been run: the inputs to a strategy.

A strategist planning a race needs a handful of numbers — how long the race is,
what a stop costs, how fast each compound falls away, how often the safety car
comes out. Every one of them is measurable from races we have already ingested,
so this module measures them, and reports each with the sample it rests on.

Anything it cannot measure is left out rather than guessed. The simulator in
core-api fills a gap with a generic figure and says it did; doing that here
would make a guess indistinguishable from a measurement by the time it reached
a reader.

Takes plain rows rather than a database handle, as ``circuit_stats`` does, so
the arithmetic can be tested against a hand-built corpus.
"""

import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The compounds a dry race is planned on. Intermediates and wets are a weather
#: call, not a plan, and are excluded from degradation fits.
DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")

#: Seconds of lap time a lap of fuel is worth. Cars get quicker as they burn
#: fuel, which hides tyre wear: a raw lap-time slope across a stint understates
#: degradation by about this much. 0.055 s/lap is the usual public figure
#: (~1.7 kg a lap at ~0.03 s/kg) and is the one assumption in the fits below.
FUEL_SECONDS_PER_LAP = 0.055

#: Fewer laps than this and a stint's slope is noise.
MIN_FIT_LAPS = 6

#: Within a stint, a lap this much slower than the stint median is traffic, a
#: yellow flag or a mistake rather than tyre wear.
OUTLIER_MULTIPLE = 1.04

#: Plausible bounds for a green-flag pit loss. Outside them the in- or out-lap
#: was disturbed (safety car, slow stop, red flag) and says nothing about the
#: pit lane itself.
PIT_LOSS_BOUNDS = (10.0, 40.0)


def _median(values: Iterable[float], digits: int = 3) -> Optional[float]:
    values = list(values)
    return round(statistics.median(values), digits) if values else None


def _race_key(row: Dict[str, Any]) -> Tuple[int, int]:
    return (row.get("season") or 0, row.get("round") or 0)


def _slope_and_intercept(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var = sum((x - mean_x) ** 2 for x, _ in points)
    if var == 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / var
    return slope, mean_y - slope * mean_x


def _stints(laps: List[Dict[str, Any]]) -> Dict[Tuple[Any, ...], List[Dict[str, Any]]]:
    """Dry-compound laps grouped by (season, round, driver, stint, compound)."""
    by_stint: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for lap in laps:
        compound = str(lap.get("compound") or "").upper()
        if compound not in DRY_COMPOUNDS or not lap.get("stint"):
            continue
        by_stint[_race_key(lap) + (lap.get("driver"), lap["stint"], compound)].append(lap)
    for rows in by_stint.values():
        rows.sort(key=lambda r: r.get("lap") or 0)
    return by_stint


def _stint_fit(rows: List[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    """(degradation s/lap, intercept at start-of-race fuel) for one stint.

    The slope is fitted against tyre age and corrected for fuel burn. The
    intercept is fuel-corrected to a common load so that stints run at
    different points in a race can be compared by compound.
    """
    # Drop the out-lap: it carries the pit exit and cold tyres, not wear. Lap 1
    # carries the start.
    timed = [
        r for r in rows[1:]
        if (r.get("lap_time_seconds") or 0) > 0 and (r.get("lap") or 0) > 1
    ]
    if len(timed) < MIN_FIT_LAPS:
        return None
    median = statistics.median(r["lap_time_seconds"] for r in timed)
    clean = [r for r in timed if r["lap_time_seconds"] <= median * OUTLIER_MULTIPLE]
    if len(clean) < MIN_FIT_LAPS:
        return None
    first_lap = rows[0].get("lap") or 0
    points = [(float(r["lap"] - first_lap), r["lap_time_seconds"]) for r in clean]
    slope, intercept = _slope_and_intercept(points)
    # A stint begun on lap 30 is ~1.6 s quicker for fuel alone and would
    # otherwise look like a faster compound.
    return slope + FUEL_SECONDS_PER_LAP, intercept + FUEL_SECONDS_PER_LAP * (first_lap - 1)


def summarise_strategy(
    circuit: str,
    results: List[Dict[str, Any]],
    laps: List[Dict[str, Any]],
    pit_stops: List[Dict[str, Any]],
    race_control: List[Dict[str, Any]],
    weather: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reduce a circuit's recent races to the inputs a strategy needs."""
    races = sorted({_race_key(r) for r in results if r.get("season")})
    profile: Dict[str, Any] = {
        "circuit": circuit,
        "races_in_sample": len(races),
        "seasons": sorted({season for season, _ in races}),
    }

    # ── Race distance ────────────────────────────────────────────────────────
    #
    # The most laps anyone completed in each race, then the median across
    # races, so one red-flagged or shortened race does not set the distance.
    last_lap: Dict[Tuple[int, int], int] = defaultdict(int)
    for lap in laps:
        key = _race_key(lap)
        last_lap[key] = max(last_lap[key], lap.get("lap") or 0)
    distances = [n for n in last_lap.values() if n > 0]
    if distances:
        profile["race_laps"] = int(statistics.median(distances))
        profile["race_laps_sample"] = len(distances)

    # ── What a stop costs ────────────────────────────────────────────────────
    #
    # In-lap plus out-lap, less two of that driver's typical laps in that race.
    # That is the whole time a stop takes out of a race, pit lane and tyre
    # warm-up included, which is the number a strategy has to pay.
    lap_time: Dict[Tuple[Any, ...], float] = {}
    driver_laps: Dict[Tuple[Any, ...], List[float]] = defaultdict(list)
    for lap in laps:
        seconds = lap.get("lap_time_seconds") or 0
        if seconds <= 0:
            continue
        key = _race_key(lap) + (lap.get("driver"),)
        lap_time[key + (lap.get("lap"),)] = seconds
        driver_laps[key].append(seconds)
    typical = {k: statistics.median(v) for k, v in driver_laps.items() if len(v) >= 10}

    losses: List[float] = []
    stops_by_driver: Dict[Tuple[Any, ...], int] = defaultdict(int)
    for stop in pit_stops:
        key = _race_key(stop) + (stop.get("driver"),)
        stops_by_driver[key] += 1
        base = typical.get(key)
        in_lap = lap_time.get(key + (stop.get("lap"),))
        out_lap = lap_time.get(key + ((stop.get("lap") or 0) + 1,))
        if not (base and in_lap and out_lap):
            continue
        loss = in_lap + out_lap - 2 * base
        if PIT_LOSS_BOUNDS[0] <= loss <= PIT_LOSS_BOUNDS[1]:
            losses.append(loss)
    if losses:
        profile["pit_loss_seconds"] = _median(losses, 1)
        profile["pit_loss_sample"] = len(losses)

    # ── How many stops the finishers made ────────────────────────────────────
    #
    # Classified finishers only: a car that retired on lap five made no stops
    # and says nothing about the strategy that worked.
    raced_with_stops = {_race_key(s) for s in pit_stops}
    counts: Dict[int, int] = defaultdict(int)
    winners: Dict[int, int] = defaultdict(int)
    for row in results:
        key = _race_key(row)
        if key not in raced_with_stops:
            continue
        if str(row.get("classified_position", "")).strip().upper() in ("R", "D", "W", "N"):
            continue
        stops = stops_by_driver.get(key + (row.get("driver"),), 0)
        if (row.get("position") or 999) <= 10:
            counts[stops] += 1
        if row.get("position") == 1:
            winners[stops] += 1
    if counts:
        profile["top_ten_stop_counts"] = {str(k): v for k, v in sorted(counts.items())}
    if winners:
        profile["winner_stop_counts"] = {str(k): v for k, v in sorted(winners.items())}

    # ── The tyres ────────────────────────────────────────────────────────────
    lengths: Dict[str, List[int]] = defaultdict(list)
    fits: Dict[str, List[float]] = defaultdict(list)
    intercepts: Dict[Tuple[int, int], Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for key, rows in _stints(laps).items():
        compound = key[4]
        # Every stint counts towards length, not only the ones long enough to
        # fit: stint length is what a team chose to do, short stints included.
        lengths[compound].append(len(rows))
        fit = _stint_fit(rows)
        if fit is not None:
            fits[compound].append(fit[0])
            intercepts[key[:2]][compound].append(fit[1])

    # Pace relative to the medium, compared inside each race so that a year
    # with a faster car or a resurfaced track does not leak into the offset.
    offsets: Dict[str, List[float]] = defaultdict(list)
    for per_compound in intercepts.values():
        if "MEDIUM" not in per_compound:
            continue
        reference = statistics.median(per_compound["MEDIUM"])
        for compound, values in per_compound.items():
            if compound != "MEDIUM":
                offsets[compound].append(statistics.median(values) - reference)

    compounds: Dict[str, Dict[str, Any]] = {}
    for compound in DRY_COMPOUNDS:
        entry: Dict[str, Any] = {}
        if lengths.get(compound):
            entry["stints"] = len(lengths[compound])
            entry["median_stint_laps"] = int(statistics.median(lengths[compound]))
            entry["longest_stint_laps"] = max(lengths[compound])
        if fits.get(compound):
            entry["degradation_seconds_per_lap"] = _median(fits[compound])
            entry["degradation_sample"] = len(fits[compound])
        if offsets.get(compound):
            entry["pace_offset_seconds"] = _median(offsets[compound], 2)
            entry["pace_offset_sample"] = len(offsets[compound])
        if entry:
            compounds[compound] = entry
    if compounds:
        profile["compounds"] = compounds

    # ── Neutralisations ──────────────────────────────────────────────────────
    #
    # Counted per race, over races whose race control feed we hold. "VIRTUAL
    # SAFETY CAR" also contains "SAFETY CAR", so the virtual one is matched
    # first and the two are kept apart.
    feed_races = {_race_key(m) for m in race_control}
    if feed_races:
        sc_races, vsc_races = set(), set()
        for message in race_control:
            text = str(message.get("message") or "").upper()
            if "DEPLOYED" not in text:
                continue
            if "VIRTUAL SAFETY CAR" in text:
                vsc_races.add(_race_key(message))
            elif "SAFETY CAR" in text:
                sc_races.add(_race_key(message))
        profile["race_control_races"] = len(feed_races)
        profile["safety_car_races"] = len(sc_races)
        profile["virtual_safety_car_races"] = len(vsc_races)

    weather_races = {_race_key(w) for w in weather}
    if weather_races:
        profile["weather_races"] = len(weather_races)
        profile["wet_races"] = len({_race_key(w) for w in weather if w.get("rainfall")})

    return profile
