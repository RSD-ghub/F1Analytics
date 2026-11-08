"""Tyres and safety cars for 2014-2017, from the TUM f1-timing-database.

jolpica's laps before 2018 have no tyre compound and no safety car periods.
The TUM database (https://github.com/TUMFTM/f1-timing-database, CC BY-NC-SA
3.0, built on Ergast) adds both for 2014-2019: the compound and tyre age on
every lap, and every SC and VSC phase with its laps.

It is read here, at training time, and laid over the stored laps; nothing is
written to the ingestion store. The file is not in this repository (licence),
so it is passed to the training script by path.

**Compounds** are stored as absolute names (A1 hardest .. A7 softest) so they
compare across seasons. A race brought two or three dry compounds; they are
mapped to the race-relative names used since 2019, softest SOFT, hardest HARD
and any between MEDIUM, which is what the lap model reads for every other
season. ``I`` and ``W`` are INTERMEDIATE and WET.

**Drivers** are matched to the stored names by surname, accents and case
ignored, as ``historical_laps`` matches jolpica; a surname two stored drivers
share is left out. **Races** are matched by season and date order, which is
the round number.
"""

import sqlite3
import unicodedata
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.ingestion_client import RaceLap

FIRST_SEASON, LAST_SEASON = 2014, 2017
WET_CODES = {"I": "INTERMEDIATE", "W": "WET"}


def _surname(name: str) -> str:
    last = (name or "").strip().split(" ")[-1]
    return "".join(c for c in unicodedata.normalize("NFKD", last)
                   if not unicodedata.combining(c)).casefold()


def relative_compounds(available: str) -> Dict[str, str]:
    """``"A2,A3,I,W"`` -> {A3: SOFT, A2: HARD, I: INTERMEDIATE, W: WET}."""
    codes = [c.strip() for c in (available or "").split(",") if c.strip()]
    dry = sorted((c for c in codes if c.startswith("A")), key=lambda c: int(c[1:]))
    out = dict(WET_CODES)
    for i, code in enumerate(dry):
        if i == len(dry) - 1:
            out[code] = "SOFT"
        elif i == 0:
            out[code] = "HARD"
        else:
            out[code] = "MEDIUM"
    return out


class RaceOverlay:
    """One race's tyres by (TUM driver name, lap), and its neutralised laps."""

    def __init__(self) -> None:
        self.compound: Dict[Tuple[str, int], str] = {}
        self.age: Dict[Tuple[str, int], int] = {}
        self.neutral: Set[int] = set()


def load(path: str, first: int = FIRST_SEASON,
         last: int = LAST_SEASON) -> Dict[Tuple[int, int], RaceOverlay]:
    """(season, round) -> RaceOverlay for every race in the file's seasons."""
    connection = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    try:
        races = connection.execute(
            "SELECT id, season, date, availablecompounds FROM races "
            "WHERE season BETWEEN ? AND ? ORDER BY season, date", (first, last)).fetchall()
        names = dict(connection.execute("SELECT id, name FROM drivers").fetchall())
        out: Dict[Tuple[int, int], RaceOverlay] = {}
        round_of: Dict[int, Tuple[int, int]] = {}
        mapping: Dict[int, Dict[str, str]] = {}
        counter: Dict[int, int] = defaultdict(int)
        for race_id, season, _, available in races:
            counter[season] += 1
            key = (int(season), counter[season])
            round_of[race_id] = key
            mapping[race_id] = relative_compounds(available)
            out[key] = RaceOverlay()
        if not round_of:
            return out
        marks = ",".join("?" * len(round_of))
        for race_id, lap, driver_id, compound, age in connection.execute(
                "SELECT race_id, lapno, driver_id, compound, tireage FROM laps "
                "WHERE race_id IN ({})".format(marks), list(round_of)):
            overlay = out[round_of[race_id]]
            name = names.get(driver_id, "")
            relative = mapping[race_id].get(compound or "")
            if relative:
                overlay.compound[(name, int(lap))] = relative
            if age is not None:
                overlay.age[(name, int(lap))] = int(age)
        for race_id, start, end in connection.execute(
                "SELECT race_id, startlap, endlap FROM fcyphases "
                "WHERE race_id IN ({})".format(marks), list(round_of)):
            if start is not None and end is not None:
                out[round_of[race_id]].neutral.update(range(int(start), int(end) + 1))
        return out
    finally:
        connection.close()


def _name_map(tum_names: Iterable[str], stored: Iterable[str]) -> Dict[str, str]:
    """TUM name -> stored name, by surname; ambiguous surnames left out."""
    by_surname: Dict[str, List[str]] = defaultdict(list)
    for name in set(stored):
        by_surname[_surname(name)].append(name)
    out = {}
    for name in set(tum_names):
        matches = by_surname.get(_surname(name), [])
        if len(matches) == 1:
            out[name] = matches[0]
    return out


def apply(laps: Sequence[RaceLap], overlay: Optional[RaceOverlay]):
    """(laps with compounds filled, tyre age by (driver, lap), neutralised laps).

    Laps the overlay does not cover keep what they had; with no overlay the
    laps come back unchanged, with no ages and no neutralised laps.
    """
    if overlay is None:
        return list(laps), {}, set()
    names = _name_map({n for n, _ in overlay.compound} | {n for n, _ in overlay.age},
                      [lap.driver for lap in laps])
    compound = {(names[n], lap): c for (n, lap), c in overlay.compound.items() if n in names}
    ages = {(names[n], lap): a for (n, lap), a in overlay.age.items() if n in names}
    out = []
    for lap in laps:
        found = compound.get((lap.driver, lap.lap))
        out.append(lap.model_copy(update={"compound": found}) if found else lap)
    return out, ages, set(overlay.neutral)


def coverage(laps: Sequence[RaceLap], ages: Mapping) -> float:
    """Share of the race's laps the overlay gave a tyre age to."""
    return sum(1 for lap in laps if (lap.driver, lap.lap) in ages) / max(len(laps), 1)
