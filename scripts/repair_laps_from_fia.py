#!/usr/bin/env python
"""Repair stored laps from the FIA's lap times. Dry run unless --apply.

    .venv/bin/python scripts/repair_laps_from_fia.py --season 2026 --round 19 \\
        --fia-json mex_lap_times.json
    .venv/bin/python scripts/repair_laps_from_fia.py ... --apply [--drop-extra]
    .venv/bin/python scripts/repair_laps_from_fia.py --restore ~/f1-backups/2026-11-08-fia-repair/2026-19

``--fia-json`` is fia-doc's lap table as records (car_no, lap, lap_time,
position), as ``scripts/fia_check.py`` parses it. Two repairs:

* **Untimed laps.** FastF1 has no time for a lap run under a red flag, and
  ``to_seconds`` stores the missing value as 0.0 (Mexico 2026, laps 45-46:
  19 laps). Where the FIA times the lap, its time goes in; the position too,
  if ours is 0. Every reader already skips 0.0 as "no time", and the pace code
  drops laps far off the median, so a 40-minute suspension lap is handled
  either way.
* **Laps past a car's last FIA lap**, only with ``--drop-extra``. FastF1 keeps
  the lap a car retired on, untimed and at position 0 (Bottas, USA 2026, lap
  49 of a classified 48), and OpenF1 did the same for Lindblad in Mexico. The
  driver's last stint counts that lap, so it loses one lap with it.

Neither repair survives a re-fetch of the race: a refresh rewrites every lap
from FastF1 and OpenF1, which brings back the 0.0 laps and the retirement laps.
Gap healing does not re-fetch a complete race; ``backfill.py full --refresh``
does. Re-run this after one.

``--apply`` first writes every document it will touch to
``~/f1-backups/<date>-fia-repair/<season>-<round>-<time>/`` as Extended JSON;
``--restore`` puts them back exactly (re-creating deleted ones).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence

LAP_TIME_TOLERANCE = 0.01
#: A car is matched to the driver whose lap times agree on at least this share
#: of the laps both have. In practice a right match agrees on all of them.
MIN_AGREEMENT = 0.8


def lap_seconds(value) -> Optional[float]:
    """``"1:22.497"`` → 82.497; anything that is not a lap time → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"^(?:(\d+):)?(\d+)\.(\d{3})$", str(value).strip())
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + int(match.group(2)) + int(match.group(3)) / 1000


# ── Pure diff ────────────────────────────────────────────────────────────────

def match_cars(fia_laps: Sequence[dict], our_laps: Sequence[dict]) -> Dict[int, str]:
    """Car number → our driver, by whose lap times agree with the car's.

    fia-doc's lap table carries car numbers and no names, and our laps carry
    names and no numbers. Two cars never post the same times over a race, so
    the times themselves are the join.
    """
    ours = defaultdict(dict)
    for row in our_laps:
        if (row.get("lap_time_seconds") or 0) > 0:
            ours[row["driver"]][int(row["lap"])] = row["lap_time_seconds"]
    theirs = defaultdict(dict)
    for row in fia_laps:
        seconds = lap_seconds(row.get("lap_time"))
        if seconds is not None:
            theirs[int(row["car_no"])][int(row["lap"])] = seconds

    mapping = {}
    for car, times in theirs.items():
        best, best_share = None, 0.0
        for driver, mine in ours.items():
            shared = set(times) & set(mine)
            if not shared:
                continue
            agree = sum(abs(times[lap] - mine[lap]) < LAP_TIME_TOLERANCE for lap in shared)
            share = agree / len(shared)
            if share > best_share:
                best, best_share = driver, share
        if best is not None and best_share >= MIN_AGREEMENT:
            mapping[car] = best
    taken = Counter(mapping.values())
    return {car: driver for car, driver in mapping.items() if taken[driver] == 1}


def plan_repairs(fia_laps: Sequence[dict], our_laps: Sequence[dict],
                 stints: Sequence[dict], drivers: Dict[int, str]) -> dict:
    """What to change. Nothing here touches the database."""
    fia = {}
    for row in fia_laps:
        driver = drivers.get(int(row["car_no"]))
        if driver is not None:
            fia[(driver, int(row["lap"]))] = row
    last_fia_lap = defaultdict(int)
    for driver, lap in fia:
        last_fia_lap[driver] = max(last_fia_lap[driver], lap)

    fills, extras = [], []
    for row in sorted(our_laps, key=lambda r: (r["driver"], int(r["lap"]))):
        key = (row["driver"], int(row["lap"]))
        theirs = fia.get(key)
        if theirs is not None and (row.get("lap_time_seconds") or 0) == 0:
            seconds = lap_seconds(theirs.get("lap_time"))
            if seconds is not None:
                change = {"lap_time_seconds": seconds}
                if not row.get("position") and theirs.get("position"):
                    change["position"] = int(theirs["position"])
                fills.append({"_id": row["_id"], "driver": key[0], "lap": key[1],
                              "fia": theirs.get("lap_time"), "set": change})
        if row["driver"] in last_fia_lap and key[1] > last_fia_lap[row["driver"]]:
            extras.append({"_id": row["_id"], "driver": key[0], "lap": key[1],
                           "stint": row.get("stint"), "lap_time_seconds": row.get("lap_time_seconds"),
                           "position": row.get("position"),
                           "fia_last_lap": last_fia_lap[row["driver"]]})

    # Each dropped lap comes off the stint it was counted in.
    removed = Counter((e["driver"], e["stint"]) for e in extras)
    stint_changes = []
    for stint in stints:
        count = removed.get((stint["driver"], stint.get("stint")))
        if count:
            left = int(stint.get("laps") or 0) - count
            stint_changes.append({"_id": stint["_id"], "driver": stint["driver"],
                                  "stint": stint.get("stint"), "laps": stint.get("laps"),
                                  "new_laps": left, "delete": left <= 0})
    return {"fills": fills, "extras": extras, "stints": stint_changes}


# ── Database ─────────────────────────────────────────────────────────────────

def _db():
    from pymongo import MongoClient

    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    return client[os.environ.get("MONGO_DATABASE", "f1_ingestion")]


def backup(db, folder: str, plan: dict) -> List[str]:
    from bson import json_util

    os.makedirs(folder, exist_ok=False)  # never overwrite an earlier backup
    written = []
    for collection, ids in (
        ("laps", [c["_id"] for c in plan["fills"]] + [e["_id"] for e in plan["extras"]]),
        ("stints", [s["_id"] for s in plan["stints"]]),
    ):
        if not ids:
            continue
        documents = list(db[collection].find({"_id": {"$in": ids}}))
        path = os.path.join(folder, collection + ".json")
        with open(path, "w") as handle:
            handle.write(json_util.dumps(documents, indent=1))
        written.append(path)
    return written


def apply_plan(db, plan: dict, drop_extra: bool) -> Dict[str, int]:
    from pymongo import DeleteOne, UpdateOne

    done = {"filled": 0, "deleted_laps": 0, "stints_changed": 0}
    if plan["fills"]:
        result = db["laps"].bulk_write(
            [UpdateOne({"_id": c["_id"]}, {"$set": c["set"]}) for c in plan["fills"]])
        done["filled"] = result.modified_count
    if drop_extra and plan["extras"]:
        result = db["laps"].bulk_write([DeleteOne({"_id": e["_id"]}) for e in plan["extras"]])
        done["deleted_laps"] = result.deleted_count
        operations = [
            DeleteOne({"_id": s["_id"]}) if s["delete"]
            else UpdateOne({"_id": s["_id"]}, {"$set": {"laps": s["new_laps"]}})
            for s in plan["stints"]
        ]
        if operations:
            result = db["stints"].bulk_write(operations)
            done["stints_changed"] = result.modified_count + result.deleted_count
    return done


def restore(db, folder: str) -> Dict[str, int]:
    from bson import json_util
    from pymongo import ReplaceOne

    restored = {}
    for collection in ("laps", "stints"):
        path = os.path.join(folder, collection + ".json")
        if not os.path.exists(path):
            continue
        with open(path) as handle:
            documents = json_util.loads(handle.read())
        if documents:
            result = db[collection].bulk_write(
                [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in documents])
            restored[collection] = result.modified_count + result.upserted_count
    return restored


# ── CLI ──────────────────────────────────────────────────────────────────────

def print_plan(plan: dict, drop_extra: bool, unmatched: Iterable[int]) -> None:
    print("fill {} untimed laps from the FIA:".format(len(plan["fills"])))
    for change in plan["fills"]:
        print("   {:<20} lap {:>2}  0.0 -> {} ({} s){}".format(
            change["driver"], change["lap"], change["fia"], change["set"]["lap_time_seconds"],
            "  position -> {}".format(change["set"]["position"]) if "position" in change["set"] else ""))
    verb = "drop" if drop_extra else "would drop with --drop-extra"
    print("{} {} laps past a car's last FIA lap:".format(verb, len(plan["extras"])))
    for extra in plan["extras"]:
        print("   {:<20} lap {:>2}  (FIA ends at {}; stored time {}, position {}, stint {})".format(
            extra["driver"], extra["lap"], extra["fia_last_lap"], extra["lap_time_seconds"],
            extra["position"], extra["stint"]))
    for stint in plan["stints"]:
        print("   stint {} of {}: {} -> {} laps{}".format(
            stint["stint"], stint["driver"], stint["laps"], stint["new_laps"],
            " (deleted)" if stint["delete"] else ""))
    unmatched = list(unmatched)
    if unmatched:
        print("cars with no matching driver (left alone): {}".format(unmatched))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--season", type=int)
    parser.add_argument("--round", type=int, dest="round_no")
    parser.add_argument("--fia-json", help="fia-doc lap table as JSON records")
    parser.add_argument("--apply", action="store_true", help="write the fills (after a backup)")
    parser.add_argument("--drop-extra", action="store_true",
                        help="with --apply, also delete laps past a car's last FIA lap")
    parser.add_argument("--backup-root", default=os.path.expanduser("~/f1-backups"))
    parser.add_argument("--restore", metavar="FOLDER", help="put back a backup and exit")
    args = parser.parse_args(argv)

    db = _db()
    if args.restore:
        print("restored:", restore(db, args.restore))
        return 0
    if not (args.season and args.round_no and args.fia_json):
        parser.error("--season, --round and --fia-json are required")

    with open(args.fia_json) as handle:
        fia_laps = json.load(handle)
    where = {"season": args.season, "round": args.round_no}
    our_laps = list(db["laps"].find(where))
    stints = list(db["stints"].find(where))
    if not our_laps:
        print("no stored laps for {} round {}".format(args.season, args.round_no))
        return 2
    drivers = match_cars(fia_laps, our_laps)
    unmatched = sorted({int(r["car_no"]) for r in fia_laps} - set(drivers))
    plan = plan_repairs(fia_laps, our_laps, stints, drivers)
    plan_for_write = plan if args.drop_extra else dict(plan, extras=[], stints=[])

    print("{} {} ({}), {} cars matched to drivers by lap time".format(
        args.season, our_laps[0].get("race_name"), "APPLY" if args.apply else "dry run",
        len(drivers)))
    print_plan(plan, args.drop_extra, unmatched)
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if not plan_for_write["fills"] and not plan_for_write["extras"]:
        print("nothing to do")
        return 0
    now = dt.datetime.now()
    folder = os.path.join(args.backup_root, "{}-fia-repair".format(now.date().isoformat()),
                          "{}-{}-{}".format(args.season, args.round_no, now.strftime("%H%M%S")))
    for path in backup(db, folder, plan_for_write):
        print("backed up", path)
    print("applied:", apply_plan(db, plan_for_write, args.drop_extra))
    print("undo: {} {} --restore {}".format(sys.executable, os.path.abspath(__file__), folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
