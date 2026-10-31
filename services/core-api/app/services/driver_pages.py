"""Driver pages and the post-race pit stop review, built from ingested rows.

Pure functions over the rows ingestion already stores, so they are testable
without a database and the router stays a thin fetch-and-call.

Two honesty rules carried over from the rest of the product:

* A retirement is not a finishing position. ``position`` on a retired row is
  the order cars stopped in, so wins, podiums and "best finish" read only
  classified rows (``classified_position`` numeric).
* Nothing is estimated. A pit stop with no recorded duration shows none, and
  a race with no stint data says so rather than drawing a one-stint race.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional


def classified(row: Dict[str, Any]) -> Optional[int]:
    """The official finishing position, or ``None`` for R/D/W/N and unknowns.

    Rows ingested before ``classified_position`` existed carry an empty
    string; for those the status decides, since "Finished" and "+1 Lap" are
    classified and everything else is not.
    """
    raw = str(row.get("classified_position") or "").strip()
    if raw.isdigit():
        return int(raw)
    if raw:
        return None
    status = str(row.get("status") or "")
    position = row.get("position") or 999
    if position < 999 and (status in ("Finished", "Lapped") or status.startswith("+")):
        return position
    return None


def _tally(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    finishes = [p for p in (classified(r) for r in rows) if p is not None]
    return {
        "starts": len(rows),
        "wins": sum(1 for p in finishes if p == 1),
        "podiums": sum(1 for p in finishes if p <= 3),
        "points_finishes": sum(1 for r in rows if (r.get("points") or 0) > 0),
        "points": round(sum(float(r.get("points") or 0) for r in rows), 1),
        "best_finish": min(finishes) if finishes else None,
        "retirements": len(rows) - len(finishes),
    }


def season_drivers(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every driver who started a race in the season, by points."""
    by_driver: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in results:
        if row.get("driver"):
            by_driver[row["driver"]].append(row)
    out = []
    for driver, rows in by_driver.items():
        latest = max(rows, key=lambda r: r.get("round") or 0)
        out.append({"driver": driver, "team": latest.get("team", ""), **_tally(rows)})
    out.sort(key=lambda d: (-d["points"], -d["wins"], d["driver"]))
    return out


def driver_profile(
    driver: str, season: int, career_rows: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """One driver's season race by race, and their career season by season."""
    if not career_rows:
        return None
    by_season: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in career_rows:
        by_season[row["season"]].append(row)

    this_season = sorted(by_season.get(season, []), key=lambda r: r.get("round") or 0)
    races = []
    for row in this_season:
        finish = classified(row)
        grid = row.get("grid_position") or 0
        races.append({
            "round": row.get("round"),
            "race_name": row.get("race_name", ""),
            "team": row.get("team", ""),
            "grid": grid or None,
            "finish": finish,
            "status": row.get("status", ""),
            "points": row.get("points") or 0,
            # Only when both ends are real: a pit-lane start or a retirement
            # has no meaningful "places gained".
            "places_gained": (grid - finish) if grid and finish else None,
        })

    career = [
        {"season": year, "team": max(rows, key=lambda r: r.get("round") or 0).get("team", ""),
         **_tally(rows)}
        for year, rows in sorted(by_season.items(), reverse=True)
    ]
    teams = [r["team"] for r in races if r["team"]]
    return {
        "driver": driver,
        "season": season,
        "team": teams[-1] if teams else (career[0]["team"] if career else ""),
        "summary": _tally(this_season) if this_season else None,
        "races": races,
        "career": career,
        "career_total": _tally(career_rows),
    }


def pit_stop_review(
    results: List[Dict[str, Any]],
    stints: List[Dict[str, Any]],
    pit_stops: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """How each car's race was run: stints, stops, and what it came to."""
    if not results:
        return None

    stints_by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in stints:
        stints_by[row.get("driver", "")].append(row)
    stops_by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in pit_stops:
        stops_by[row.get("driver", "")].append(row)

    drivers = []
    for row in sorted(results, key=lambda r: (classified(r) or 999, r.get("position") or 999)):
        name = row.get("driver", "")
        lap = 1
        plan = []
        for stint in sorted(stints_by.get(name, []), key=lambda s: s.get("stint") or 0):
            laps = int(stint.get("laps") or 0)
            plan.append({
                "compound": stint.get("compound") or "Unknown",
                "laps": laps,
                "from_lap": lap,
                "to_lap": lap + laps - 1 if laps else lap,
            })
            lap += laps
        stops = [
            {
                "lap": s.get("lap"),
                "duration_seconds": s.get("duration_seconds") or None,
            }
            for s in sorted(stops_by.get(name, []), key=lambda s: s.get("stop") or 0)
        ]
        finish = classified(row)
        grid = row.get("grid_position") or 0
        drivers.append({
            "driver": name,
            "team": row.get("team", ""),
            "grid": grid or None,
            "finish": finish,
            "status": row.get("status", ""),
            "places_gained": (grid - finish) if grid and finish else None,
            "stints": plan,
            "stops": stops,
        })

    timed = [
        {"driver": d["driver"], "team": d["team"], "lap": s["lap"],
         "duration_seconds": s["duration_seconds"]}
        for d in drivers for s in d["stops"] if s["duration_seconds"]
    ]
    timed.sort(key=lambda s: s["duration_seconds"])

    # Stop count against result, finishers only: a car that retired on lap 3
    # "made no stops" in a sense that says nothing about strategy.
    groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for d in drivers:
        if d["finish"] is not None and d["stints"]:
            groups[len(d["stops"])].append(d)
    strategies = []
    for count, members in sorted(groups.items()):
        gains = [m["places_gained"] for m in members if m["places_gained"] is not None]
        strategies.append({
            "stops": count,
            "drivers": len(members),
            "best_finish": min(m["finish"] for m in members),
            "mean_places_gained": round(sum(gains) / len(gains), 1) if gains else None,
        })

    sample = results[0]
    total_laps = max((s["to_lap"] for d in drivers for s in d["stints"]), default=0)
    return {
        "season": sample.get("season"),
        "round": sample.get("round"),
        "race_name": sample.get("race_name", ""),
        "laps": total_laps or None,
        "has_stint_data": any(d["stints"] for d in drivers),
        "drivers": drivers,
        "fastest_stops": timed[:5],
        "strategies": strategies,
    }
