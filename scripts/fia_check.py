#!/usr/bin/env python
"""Check a stored race against the FIA's own timing documents.

    ~/fiadoc-venv/bin/python scripts/fia_check.py --season 2026 --round 19
    ~/fiadoc-venv/bin/python scripts/fia_check.py --season 2026 --round 17 --sprint
    ~/fiadoc-venv/bin/python scripts/fia_check.py --season 2026 --round 19 --json out.json

Our laps, pit stops and results come from FastF1, with OpenF1 filling in
where the official feed stops short. Every integrity check the ingestion
service runs compares those sources with themselves. This one compares them
with a source that shares nothing with them: the PDFs the FIA timekeepers
publish, parsed by fia-doc (github.com/harningle/fia-doc, the parser behind
Jolpica's own imports).

Checks, each with a count and its mismatches:

* lap times within 0.01 s, lap 1 included (fia-doc reads it off the History
  Chart; the Lap Analysis prints a time of day there)
* position at the end of every lap
* pit stops, matched on driver and lap
* classification position and points — only against the stewards' *Final*
  Race Classification. The provisional sheet on the timing page predates
  post-race penalties: checked against it, Mexico 2026 reported Piastri,
  Colapinto and Gasly "wrong" when our order was the penalised, final one.

And two flags for things that are ours to fix rather than disagreements:
laps stored as 0.0 s that the FIA times (red-flag laps), and laps stored past
a car's last FIA lap (a retired car's extra lap).

Exit code 0 when clean, 1 when anything disagrees or is flagged, 2 when the
FIA documents are not available yet. Reads Mongo only; writes nothing.

fia-doc pulls in paddlepaddle and OCR models (about 1.2 GB), so it lives in a
venv of its own; see scripts/requirements-fia-check.txt.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

TIMING_ROOT = "https://www.fia.com/sites/default/files"
DECISION_ROOT = "https://www.fia.com/system/files/decision-document"

#: FIA's three-letter event codes. They are not derivable from the event name
#: (Miami files as ``usa``, the 2026 Bahrain race at Sepang as ``brn``), so the
#: script tries each against the round's Lap Analysis until one exists.
EVENT_CODES = (
    "aus", "chn", "jpn", "bhr", "brn", "ksa", "usa", "mia", "emi", "mon",
    "esp", "can", "aut", "gbr", "bel", "hun", "ned", "ita", "aze", "sgp",
    "mex", "bra", "las", "lvg", "qat", "abu", "uae", "mad",
)

LAP_TIME_TOLERANCE = 0.01  # seconds; FIA prints milliseconds
EXIT_CLEAN, EXIT_MISMATCH, EXIT_UNAVAILABLE = 0, 1, 2


class DocumentsUnavailable(RuntimeError):
    """The FIA has not published (or we cannot find) this session's PDFs."""


# ── Pure comparison ──────────────────────────────────────────────────────────

def norm_surname(name: str) -> str:
    """``"Nico Hülkenberg"`` and ``"Nico HULKENBERG"`` both become ``HULKENBERG``."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    parts = plain.upper().split()
    return parts[-1] if parts else ""


def lap_seconds(value) -> Optional[float]:
    """``"1:22.497"`` → 82.497. A time of day or anything else → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"^(?:(\d+):)?(\d+)\.(\d{3})$", str(value).strip())
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + int(match.group(2)) + int(match.group(3)) / 1000


def car_drivers(car_names: Dict[int, str], our_drivers: Iterable[str]) -> Tuple[Dict[int, str], List[int]]:
    """Car number → our driver name, by surname. Also the cars left unmatched."""
    by_surname: Dict[str, List[str]] = {}
    for driver in our_drivers:
        by_surname.setdefault(norm_surname(driver), []).append(driver)
    mapping, unmatched = {}, []
    for car, name in car_names.items():
        candidates = by_surname.get(norm_surname(name), [])
        if len(candidates) == 1:
            mapping[car] = candidates[0]
        else:
            unmatched.append(car)
    return mapping, sorted(unmatched)


def _check(name: str) -> dict:
    return {"check": name, "compared": 0, "matched": 0, "mismatches": []}


def compare_laps(fia_laps: Sequence[dict], our_laps: Sequence[dict],
                 drivers: Dict[int, str]) -> dict:
    """Lap times, lap 1, positions, and the two flags.

    ``fia_laps`` rows are fia-doc's: car_no, lap, lap_time ("1:22.497"),
    position. ``our_laps`` rows are Mongo's: driver, lap, lap_time_seconds,
    position.
    """
    ours = {(row["driver"], int(row["lap"])): row for row in our_laps}
    theirs = {}
    for row in fia_laps:
        driver = drivers.get(int(row["car_no"]))
        if driver is not None:
            theirs[(driver, int(row["lap"]))] = row

    times, lap_one, positions = _check("lap_time"), _check("lap_1_time"), _check("position")
    zero_laps, missing_from_ours = [], []
    for key, fia in sorted(theirs.items()):
        driver, lap = key
        stored = ours.get(key)
        if stored is None:
            missing_from_ours.append({"driver": driver, "lap": lap})
            continue
        fia_time = lap_seconds(fia.get("lap_time"))
        our_time = stored.get("lap_time_seconds")
        if fia_time is not None and our_time is not None:
            if our_time == 0:
                zero_laps.append({"driver": driver, "lap": lap, "fia": fia.get("lap_time")})
            else:
                bucket = lap_one if lap == 1 else times
                bucket["compared"] += 1
                if abs(fia_time - our_time) < LAP_TIME_TOLERANCE:
                    bucket["matched"] += 1
                else:
                    bucket["mismatches"].append({
                        "driver": driver, "lap": lap, "fia": fia.get("lap_time"),
                        "ours": our_time, "diff": round(abs(fia_time - our_time), 3),
                    })
        if fia.get("position") is not None and stored.get("position") is not None:
            positions["compared"] += 1
            if int(fia["position"]) == int(stored["position"]):
                positions["matched"] += 1
            else:
                positions["mismatches"].append({
                    "driver": driver, "lap": lap,
                    "fia": int(fia["position"]), "ours": int(stored["position"]),
                })

    last_fia_lap: Dict[str, int] = {}
    for driver, lap in theirs:
        last_fia_lap[driver] = max(lap, last_fia_lap.get(driver, 0))
    beyond = [
        {"driver": driver, "lap": lap, "fia_last_lap": last_fia_lap[driver],
         "position": row.get("position")}
        for (driver, lap), row in sorted(ours.items())
        if driver in last_fia_lap and lap > last_fia_lap[driver]
    ]
    if missing_from_ours:
        times["mismatches"].extend(
            {"driver": m["driver"], "lap": m["lap"], "issue": "FIA lap not stored"}
            for m in missing_from_ours
        )
    return {
        "checks": [times, lap_one, positions],
        "flags": {"zero_time_laps": zero_laps, "laps_past_fia_last_lap": beyond},
    }


def compare_pit_stops(fia_stops: Sequence[dict], our_stops: Sequence[dict],
                      drivers: Dict[int, str]) -> dict:
    """Stops matched on driver and lap. Durations are reported, not judged:
    the FIA's pit-lane time and ours differ by up to a quarter second."""
    check = _check("pit_stop")
    theirs = {(drivers.get(int(s["car_no"])), int(s["lap"])): s for s in fia_stops}
    ours = {(s["driver"], int(s["lap"])): s for s in our_stops}
    worst = 0.0
    for key in sorted(set(theirs) | set(ours), key=str):
        driver, lap = key
        if key in theirs and key in ours:
            check["compared"] += 1
            check["matched"] += 1
            fia_s, our_s = lap_seconds(theirs[key].get("duration")), ours[key].get("duration_seconds")
            if fia_s is not None and our_s is not None:
                worst = max(worst, abs(fia_s - our_s))
        else:
            check["compared"] += 1
            check["mismatches"].append({
                "driver": driver if driver else "car {}".format(theirs[key]["car_no"]),
                "lap": lap, "issue": "only in FIA" if key in theirs else "only in ours",
            })
    check["max_duration_diff_s"] = round(worst, 3)
    return check


def compare_classification(fia_rows: Sequence[dict], our_rows: Sequence[dict],
                           drivers: Dict[int, str]) -> dict:
    check = _check("classification")
    ours = {row["driver"]: row for row in our_rows}
    for row in fia_rows:
        driver = drivers.get(int(row["car_no"]))
        stored = ours.get(driver)
        check["compared"] += 1
        if stored is None:
            check["mismatches"].append({"car_no": int(row["car_no"]), "issue": "no stored result"})
            continue
        fia = (int(row["finishing_position"]), float(row.get("points") or 0))
        mine = (int(stored["position"]), float(stored.get("points") or 0))
        if fia == mine:
            check["matched"] += 1
        else:
            check["mismatches"].append({
                "driver": driver, "fia": {"position": fia[0], "points": fia[1]},
                "ours": {"position": mine[0], "points": mine[1]},
            })
    return check


def is_clean(report: dict) -> bool:
    if any(c["mismatches"] for c in report["checks"]):
        return False
    return not any(report.get("flags", {}).values())


# ── FIA documents ────────────────────────────────────────────────────────────

class RateLimited(DocumentsUnavailable):
    """fia.com answered 429. Its CDN then serves a browser challenge for a while;
    the only right response is to stop and try again later."""


#: Seconds between requests to fia.com. Probing every event code back to back
#: got this machine rate-limited within a few dozen requests.
REQUEST_GAP = 1.5
#: A whole download, not one socket read. ``requests``' own timeout restarts
#: with every chunk, so a response that trickles in would never trip it.
REQUEST_DEADLINE = 90
_last_request = [0.0]


def _get(url: str):
    """The PDF at ``url``, or None when it is not published (or will not arrive)."""
    import time

    import requests

    wait = _last_request[0] + REQUEST_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = started = time.monotonic()
    with requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=(10, 30),
                      stream=True) as response:
        if response.status_code == 429:
            raise RateLimited("fia.com is rate-limiting this machine (HTTP 429); try again later")
        if response.status_code != 200:
            return None
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            body.extend(chunk)
            if time.monotonic() - started > REQUEST_DEADLINE:
                print("gave up on {} after {} s".format(url, REQUEST_DEADLINE), file=sys.stderr)
                return None
    return bytes(body) if body[:5] == b"%PDF-" else None


#: Codes worth trying first for an event, by a word in its name. Saves probing
#: the whole list (one request each) for every race.
_CODE_HINTS = (
    ("australia", "aus"), ("chin", "chn"), ("japan", "jpn"), ("bahrain", "brn"),
    ("bahrain", "bhr"), ("saudi", "ksa"), ("miami", "usa"), ("emilia", "emi"),
    ("monaco", "mon"), ("barcelona", "esp"), ("spanish", "esp"), ("canad", "can"),
    ("austria", "aut"), ("british", "gbr"), ("belgian", "bel"), ("hungar", "hun"),
    ("dutch", "ned"), ("ital", "ita"), ("azerbaijan", "aze"), ("singapore", "sgp"),
    ("united states", "usa"), ("mexico", "mex"), ("paulo", "bra"), ("vegas", "las"),
    ("qatar", "qat"), ("abu dhabi", "abu"), ("madrid", "mad"),
)


def codes_to_try(event_name: str) -> List[str]:
    """Event codes in the order to probe them: hinted ones first, then the rest."""
    name = (event_name or "").lower()
    hinted = [code for word, code in _CODE_HINTS if word in name]
    ordered = list(dict.fromkeys(hinted + list(EVENT_CODES)))
    return ordered


def _event_slug(event_name: str) -> str:
    plain = unicodedata.normalize("NFKD", event_name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def download_documents(season: int, round_no: int, event_name: str, sprint: bool,
                       folder: str, code: Optional[str] = None) -> dict:
    """Fetch the session's timing PDFs and, if published, its final classification."""
    prefix, kind = ("s0", "sprint") if sprint else ("r0", "race")
    names = {
        "lap_analysis": kind + "lapanalysis",
        "history_chart": kind + "historychart",
        "lap_chart": kind + "lapchart",
        "pit_stops": kind + "pitstopsummary",
        "provisional": kind + "provisionalclassification",
    }

    def timing_url(event_code: str, doc: str, version: int) -> str:
        return "{}/{}_{:02d}_{}_f1_{}_timing_{}_v{:02d}.pdf".format(
            TIMING_ROOT, season, round_no, event_code, prefix, doc, version)

    codes = [code] if code else codes_to_try(event_name)
    found = None
    for event_code in codes:
        lap_analysis = _get(timing_url(event_code, names["lap_analysis"], 1))
        if lap_analysis:
            found = event_code
            break
    if found is None:
        raise DocumentsUnavailable(
            "no {} Lap Analysis for {} round {} under any known event code".format(
                kind, season, round_no))

    paths = {"event_code": found}
    for key, doc in names.items():
        # A reissue goes up as v02, v03…; walk up until one is missing and
        # keep the newest.
        data, version = None, 1
        while version <= 5:
            newer = _get(timing_url(found, doc, version))
            if newer is None:
                break
            data, version = newer, version + 1
        if data is None:
            if key == "provisional":
                continue
            raise DocumentsUnavailable("{} not published yet".format(doc))
        path = os.path.join(folder, "{}.pdf".format(key))
        with open(path, "wb") as handle:
            handle.write(data)
        paths[key] = path

    final = _get("{}/{}_{}_-_final_{}_classification.pdf".format(
        DECISION_ROOT, season, _event_slug(event_name), kind))
    if final:
        paths["final"] = os.path.join(folder, "final.pdf")
        with open(paths["final"], "wb") as handle:
            handle.write(final)
    return paths


def car_names_from_lap_analysis(path: str) -> Dict[int, str]:
    """Each Lap Analysis block opens with the car number, the driver, then LAP."""
    import pymupdf

    names = {}
    for page in pymupdf.open(path):
        for number, name in re.findall(r"(?m)^(\d{1,2})\n([^\n]+)\nLAP\n", page.get_text()):
            # A lap row ("12" then "1:46.078") can sit right above the next
            # block's LAP header; only a line with a surname is a driver.
            if re.search(r"[A-Z]{2,}", name) and not lap_seconds(name):
                names.setdefault(int(number), name.strip())
    return names


def parse_documents(paths: dict, season: int, round_no: int, sprint: bool) -> dict:
    from fiadoc.parser import PitStopParser, RaceParser
    import pandas as pd

    session = "sprint" if sprint else "race"
    classification = paths.get("final") or paths.get("provisional")
    if classification is None:
        raise DocumentsUnavailable("no classification PDF to anchor the parse")
    race = RaceParser(classification, paths["lap_analysis"], paths["history_chart"],
                      paths["lap_chart"], None, season, round_no, session)

    def records(frame) -> List[dict]:
        # fia-doc replaces DataFrame.to_json with its Jolpica exporter.
        return json.loads(pd.DataFrame.to_json(frame, orient="records", default_handler=str))

    return {
        "laps": records(race.lap_times_df),
        "classification": records(race.classification_df) if "final" in paths else None,
        "pit_stops": records(PitStopParser(paths["pit_stops"], season, round_no, session).df),
        "car_names": car_names_from_lap_analysis(paths["lap_analysis"]),
    }


# ── Ours (read only) ─────────────────────────────────────────────────────────

def read_ours(season: int, round_no: int, sprint: bool) -> dict:
    from pymongo import MongoClient

    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    db = client[os.environ.get("MONGO_DATABASE", "f1_ingestion")]
    where = {"season": season, "round": round_no}
    projection = {"_id": 0}
    results = list(db["sprint_results" if sprint else "results"].find(where, projection))
    # Laps and pit stops are stored for the Grand Prix only.
    laps = [] if sprint else list(db["laps"].find(where, projection))
    stops = [] if sprint else list(db["pit_stops"].find(where, projection))
    return {"results": results, "laps": laps, "pit_stops": stops}


# ── Report ───────────────────────────────────────────────────────────────────

def build_report(fia: dict, ours: dict, has_final: bool) -> dict:
    drivers_ours = {r["driver"] for r in ours["results"]} | {r["driver"] for r in ours["laps"]}
    drivers, unmatched = car_drivers(fia["car_names"], drivers_ours)
    report = {"unmatched_cars": unmatched, "checks": [], "flags": {}, "notes": []}
    if ours["laps"]:
        laps = compare_laps(fia["laps"], ours["laps"], drivers)
        report["checks"] += laps["checks"]
        report["flags"] = laps["flags"]
        report["checks"].append(compare_pit_stops(fia["pit_stops"], ours["pit_stops"], drivers))
    else:
        report["notes"].append("no stored laps for this session; lap and pit checks skipped")
    if has_final:
        report["checks"].append(compare_classification(fia["classification"], ours["results"], drivers))
    else:
        report["notes"].append(
            "only the provisional classification is published; classification "
            "check skipped (it predates post-race penalties)")
    if unmatched:
        report["checks"].append({"check": "car_mapping", "compared": len(fia["car_names"]),
                                 "matched": len(drivers),
                                 "mismatches": [{"car_no": c, "fia_name": fia["car_names"][c]} for c in unmatched]})
    return report


def print_report(report: dict, header: str, limit: int = 20) -> None:
    print(header)
    for note in report["notes"]:
        print("  note:", note)
    for check in report["checks"]:
        extra = ""
        if "max_duration_diff_s" in check:
            extra = "  (max duration diff {} s)".format(check["max_duration_diff_s"])
        print("  {:<15} {:>5}/{:<5} match{}".format(
            check["check"], check["matched"], check["compared"], extra))
        for row in check["mismatches"][:limit]:
            print("      ", json.dumps(row, default=str))
        if len(check["mismatches"]) > limit:
            print("       … {} more".format(len(check["mismatches"]) - limit))
    for name, rows in report["flags"].items():
        print("  flag {:<22} {}".format(name, len(rows)))
        for row in rows[:limit]:
            print("      ", json.dumps(row, default=str))
    if is_clean(report):
        verdict = "clean"
    elif any(c["mismatches"] for c in report["checks"]):
        verdict = "MISMATCHES"
    else:
        verdict = "agrees with FIA; flagged rows to fix on our side"
    print("  result:", verdict)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--round", type=int, required=True, dest="round_no")
    parser.add_argument("--sprint", action="store_true")
    parser.add_argument("--code", help="FIA event code (e.g. mex) if probing fails")
    parser.add_argument("--json", dest="json_path", help="also write the report here")
    parser.add_argument("--keep-pdfs", help="save the downloaded PDFs in this folder")
    args = parser.parse_args(argv)

    ours = read_ours(args.season, args.round_no, args.sprint)
    if not ours["results"]:
        print("nothing stored for {} round {}".format(args.season, args.round_no))
        return EXIT_UNAVAILABLE
    event_name = ours["results"][0]["race_name"]
    folder = args.keep_pdfs or tempfile.mkdtemp(prefix="fia_check_")
    os.makedirs(folder, exist_ok=True)
    try:
        paths = download_documents(args.season, args.round_no, event_name, args.sprint,
                                   folder, args.code)
        fia = parse_documents(paths, args.season, args.round_no, args.sprint)
    except DocumentsUnavailable as exc:
        print("FIA documents unavailable: {}".format(exc))
        return EXIT_UNAVAILABLE

    report = build_report(fia, ours, "final" in paths)
    report.update(season=args.season, round=args.round_no, event=event_name,
                  session="sprint" if args.sprint else "race",
                  classification_source="final" if "final" in paths else "provisional only",
                  event_code=paths["event_code"])
    print_report(report, "{} {} {} (FIA code {}, classification: {})".format(
        args.season, event_name, report["session"], paths["event_code"],
        report["classification_source"]))
    if args.json_path:
        with open(args.json_path, "w") as handle:
            json.dump(report, handle, indent=1, default=str)
    return EXIT_CLEAN if is_clean(report) else EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
