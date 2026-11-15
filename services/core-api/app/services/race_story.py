"""A finished Grand Prix lap by lap, with what we forecast beside it.

The data the Race page draws: every car's place and gap to the leader at the
end of each lap, its tyre and stops, the laps run under a safety car, VSC or
red flag, the race control messages, and every forecast we locked before the
start. All of it is read from what ingestion already stores; nothing is
simulated here. The lap-by-lap live forecast is the Live page's replay, which
the page asks for separately because it takes seconds to build.

**Gaps are summed lap times**, so a car's gap at lap n is its race time to the
end of lap n minus the leader's. A lap with no time (a red-flag lap stored as
0.0 s, or a gap in the feed) makes that car's gap unknown from then on rather
than wrong; positions still come from the timing rows.

**Neutralised laps** follow the same race control messages the live state
reads (``live_state.neutralisation`` in prediction-service), simplified: a
safety car or VSC runs from DEPLOYED to IN THIS LAP / ENDING, a red flag from
the flag to the restart.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from app.services.driver_pages import classified

#: Forecast windows in the order they lock.
WINDOWS = ("pre_quali", "post_quali", "final_grid")


def neutral_laps(messages: Sequence[Dict[str, Any]], total: int) -> Dict[int, str]:
    """``{lap: "SC" | "VSC" | "RED"}`` for every neutralised lap."""
    rows = sorted((m for m in messages if m.get("lap")), key=lambda m: m["lap"])
    out: Dict[int, str] = {}
    open_from: Dict[str, int] = {}

    def close(kind: str, lap: int) -> None:
        start = open_from.pop(kind, None)
        if start is None:
            return
        for n in range(start, lap + 1):
            if out.get(n) != "RED":
                out[n] = kind

    for message in rows:
        text = (message.get("message") or "").upper()
        category = message.get("category") or ""
        lap = int(message["lap"])
        if category == "SafetyCar":
            if text.startswith("VIRTUAL SAFETY CAR DEPLOYED"):
                open_from["VSC"] = lap
            elif text.startswith("VIRTUAL SAFETY CAR ENDING"):
                close("VSC", lap)
            elif text.startswith("SAFETY CAR DEPLOYED"):
                open_from["SC"] = lap
            elif text.startswith("SAFETY CAR IN THIS LAP"):
                close("SC", lap)
        elif category == "Flag" and text.startswith("RED FLAG"):
            out[lap] = "RED"
            open_from["SC"] = lap
        elif "SC" in open_from and lap > open_from["SC"] and (
                text.startswith("ROLLING START") or text.startswith("DRS ENABLED")):
            close("SC", lap - 1)
    for kind in list(open_from):
        close(kind, total)
    return out


def _forecasts(predictions: Sequence[Dict[str, Any]], finish: Dict[str, Optional[int]]) -> List[Dict[str, Any]]:
    """Each locked Grand Prix window, its favourites and how they came out."""
    out = []
    by_window = {p.get("window"): p for p in predictions}
    for window in WINDOWS:
        p = by_window.get(window)
        if not p:
            continue
        rows = p.get("driver_probabilities") or []
        markets = p.get("published_markets") or []
        winner = next((d for d, f in finish.items() if f == 1), None)
        podium = {d for d, f in finish.items() if f is not None and f <= 3}
        by_podium = sorted(rows, key=lambda r: -(r.get("p_podium") or 0.0))
        top3 = [r["driver"] for r in by_podium[:3]]
        favourite = None
        if "win" in markets:
            best = max(rows, key=lambda r: r.get("p_win") or 0.0, default=None)
            favourite = best["driver"] if best else None
        out.append({
            "window": window,
            "locked_at": p.get("locked_at"),
            "model_version": p.get("model_version"),
            "markets": markets,
            "favourite": favourite,
            "favourite_won": (favourite == winner) if favourite and winner else None,
            "winner_chance": next((r.get("p_win") for r in rows if r["driver"] == winner), None)
            if "win" in markets else None,
            "podium_picks": top3,
            "podium_hits": sum(1 for d in top3 if d in podium),
            "drivers": {
                r["driver"]: {
                    "win": r.get("p_win") if "win" in markets else None,
                    "podium": r.get("p_podium"),
                    "points": r.get("p_points"),
                    "expected_position": r.get("expected_position"),
                }
                for r in rows
            },
        })
    return out


def race_story(
    results: Sequence[Dict[str, Any]],
    laps: Sequence[Dict[str, Any]],
    pit_stops: Sequence[Dict[str, Any]],
    messages: Sequence[Dict[str, Any]],
    predictions: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Everything the Race page draws for one finished Grand Prix."""
    if not results:
        return None
    order = sorted(results, key=lambda r: (classified(r) or 999, r.get("position") or 999))
    names = [r.get("driver", "") for r in order]
    finish = {r.get("driver", ""): classified(r) for r in order}

    laps_by: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)
    for row in laps:
        if row.get("lap"):
            laps_by[row.get("driver", "")][int(row["lap"])] = row
    total = max((n for rows in laps_by.values() for n in rows), default=0)

    stops_by: Dict[str, List[int]] = defaultdict(list)
    for row in pit_stops:
        if row.get("lap"):
            stops_by[row.get("driver", "")].append(int(row["lap"]))

    # Race time to the end of each lap; None once a lap has no time.
    elapsed: Dict[str, List[Optional[float]]] = {}
    for name in names:
        running: Optional[float] = 0.0
        series: List[Optional[float]] = []
        for n in range(1, total + 1):
            row = laps_by[name].get(n)
            seconds = (row or {}).get("lap_time_seconds") or 0.0
            if row is None:
                series.append(None)
                continue
            running = running + seconds if (running is not None and seconds > 0) else None
            series.append(running)
        elapsed[name] = series

    drivers = []
    for result in order:
        name = result.get("driver", "")
        rows = laps_by[name]
        positions, gaps, compounds, ages = [], [], [], []
        age, last_stint = 0, None
        for n in range(1, total + 1):
            row = rows.get(n)
            if row is None:
                positions.append(None); gaps.append(None); compounds.append(None); ages.append(None)
                continue
            positions.append(row.get("position") or None)
            leader = [elapsed[d][n - 1] for d in names
                      if laps_by[d].get(n, {}).get("position") == 1]
            mine = elapsed[name][n - 1]
            gaps.append(round(mine - leader[0], 3) if leader and leader[0] is not None and mine is not None else None)
            stint = row.get("stint")
            age = 1 if stint != last_stint else age + 1
            last_stint = stint
            compounds.append((row.get("compound") or "").upper() or None)
            ages.append(age)
        last_lap = max(rows) if rows else 0
        drivers.append({
            "driver": name,
            "team": result.get("team", ""),
            "grid": result.get("grid_position") or None,
            "finish": finish[name],
            "status": result.get("status", ""),
            "last_lap": last_lap,
            "positions": positions,
            "gaps": gaps,
            "compounds": compounds,
            "tyre_ages": ages,
            "stops": sorted(stops_by.get(name, [])),
        })

    sample = order[0]
    return {
        "season": sample.get("season"),
        "round": sample.get("round"),
        "race_name": sample.get("race_name", ""),
        "total_laps": total,
        "has_laps": total > 0,
        "drivers": drivers,
        "neutral": {str(k): v for k, v in sorted(neutral_laps(messages, total).items())},
        "messages": [
            {"lap": m.get("lap"), "category": m.get("category", ""), "message": m.get("message", "")}
            for m in sorted((m for m in messages if m.get("lap")), key=lambda m: m["lap"])
            if (m.get("category") or "") in ("SafetyCar", "Flag", "CarEvent", "Other", "Drs")
        ],
        "forecasts": _forecasts(predictions, finish),
    }
