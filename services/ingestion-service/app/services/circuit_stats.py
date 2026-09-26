"""What our own corpus knows about a circuit.

Separate from ``circuit_map``, which is geometry taken from FastF1 telemetry.
Everything here is computed from races we have already ingested, so a number on
the page can be traced to results and laps in this database rather than to a
reference work nobody can check.

The stats are chosen to answer what a viewer actually wonders before a race —
does pole matter here, does the order ever change, does the field finish — and
each one is reported with the sample it rests on. Nine visits is a small
number and the page should say nine rather than implying a law of the track.
"""

import logging
import statistics
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Below this many races, a rate is quoted with its raw counts and nothing is
#: called typical. Two visits cannot establish a character.
MIN_RACES_FOR_A_RATE = 4


def _median(values: List[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


def _was_classified(row: Dict[str, Any]) -> bool:
    """Did this car see the flag?

    ``position`` is the wrong field to ask. A retirement still carries an
    ordinal there — where the car fell in the final order — so testing it made
    every driver at every circuit a finisher, and Baku, which retires roughly
    one car in five, reported a finish rate of exactly 1.000. It is
    ``classified_position`` that holds "R".
    """
    return str(row.get("classified_position", "")).strip().upper() != "R"


def summarise(
    circuit: str,
    results: List[Dict[str, Any]],
    laps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reduce a circuit's races and laps to the handful of facts worth showing.

    Takes plain rows rather than a database handle so the arithmetic can be
    tested against a hand-built corpus — the numbers here are the kind that
    look reasonable while being quietly wrong.
    """
    seasons = sorted({r["season"] for r in results if r.get("season")})
    races = {(r["season"], r["round"]) for r in results if r.get("season")}

    stats: Dict[str, Any] = {
        "circuit": circuit,
        "races_in_corpus": len(races),
        "first_season": seasons[0] if seasons else None,
        "last_season": seasons[-1] if seasons else None,
    }

    # ── Does pole matter here ────────────────────────────────────────────────
    #
    # Counted over races where we actually hold a pole sitter, not over every
    # race in the corpus: a missing grid would otherwise read as pole losing.
    poles, pole_wins = 0, 0
    for season, round_number in races:
        field = [
            r for r in results
            if r["season"] == season and r["round"] == round_number
        ]
        sitter = next((r for r in field if (r.get("grid_position") or 0) == 1), None)
        if sitter is None:
            continue
        poles += 1
        if (sitter.get("position") or 0) == 1:
            pole_wins += 1
    if poles:
        stats["pole_starts"] = poles
        stats["pole_wins"] = pole_wins
        if poles >= MIN_RACES_FOR_A_RATE:
            stats["pole_win_rate"] = round(pole_wins / poles, 3)

    # ── Does the order change ────────────────────────────────────────────────
    #
    # Mean absolute places between the grid slot and the finish, over cars that
    # were classified. Retirements are excluded deliberately: a car that stops
    # on lap three "loses" fifteen places and did no overtaking to do it, which
    # would turn an attrition circuit into a fictional overtaking one.
    moves = [
        abs((r.get("grid_position") or 0) - (r.get("position") or 0))
        for r in results
        if (r.get("grid_position") or 0) > 0
        and (r.get("position") or 0) > 0
        and _was_classified(r)
    ]
    if moves:
        stats["mean_places_changed"] = round(sum(moves) / len(moves), 2)
        stats["places_changed_sample"] = len(moves)

    # ── Does the field finish ────────────────────────────────────────────────
    classified = [r for r in results if _was_classified(r)]
    if results:
        stats["finish_rate"] = round(len(classified) / len(results), 3)
        stats["starts_counted"] = len(results)

    # ── Pace ─────────────────────────────────────────────────────────────────
    times = [
        l["lap_time_seconds"] for l in laps
        if (l.get("lap_time_seconds") or 0) > 0
    ]
    if times:
        stats["median_lap_seconds"] = _median(times)
        best = min(laps, key=lambda l: l.get("lap_time_seconds") or 1e9)
        stats["fastest_lap"] = {
            "seconds": round(best["lap_time_seconds"], 3),
            "driver": best.get("driver", "?"),
            "season": best.get("season"),
        }
        stats["laps_sampled"] = len(times)

    traps = [l["speed_trap_kph"] for l in laps if (l.get("speed_trap_kph") or 0) > 0]
    if traps:
        stats["median_speed_trap_kph"] = int(statistics.median(traps))
        stats["top_speed_kph"] = int(max(traps))

    # ── Who owns the place ───────────────────────────────────────────────────
    wins: Dict[str, int] = {}
    for row in results:
        if (row.get("position") or 0) == 1 and row.get("driver"):
            wins[row["driver"]] = wins.get(row["driver"], 0) + 1
    if wins:
        best_driver = max(wins.items(), key=lambda kv: kv[1])
        # Only worth calling out when somebody is actually ahead. A three-way
        # tie on one win each is not a driver who owns the circuit.
        if best_driver[1] > 1:
            stats["most_wins"] = {"driver": best_driver[0], "wins": best_driver[1]}

    return stats
