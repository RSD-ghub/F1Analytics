"""TUM timing overlay: compounds made race-relative, drivers and races matched."""

import sqlite3

from app.services.ingestion_client import RaceLap
from app.training import tum_timing


def _database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE races (id INTEGER, season INTEGER, date TEXT, location TEXT,
                            availablecompounds TEXT);
        CREATE TABLE drivers (id INTEGER, carno INTEGER, initials TEXT, name TEXT);
        CREATE TABLE laps (race_id INTEGER, lapno INTEGER, position INTEGER, driver_id INTEGER,
                           laptime REAL, compound TEXT, tireage INTEGER);
        CREATE TABLE fcyphases (id INTEGER, race_id INTEGER, startlap INTEGER, endlap INTEGER,
                                type TEXT);
        INSERT INTO races VALUES (7, 2015, '2015-04-12', 'Shanghai', 'A2,A3,I,W'),
                                 (6, 2015, '2015-03-15', 'Melbourne', 'A1,A2,A3,I,W');
        INSERT INTO drivers VALUES (1, 44, 'HAM', 'Lewis Hamilton'),
                                   (2, 27, 'HUL', 'Nico Hulkenberg');
        INSERT INTO laps VALUES (6, 1, 1, 1, 100.0, 'A3', 2), (6, 2, 1, 1, 95.0, 'A1', 1),
                                (6, 1, 2, 2, 101.0, 'I', 1), (7, 1, 1, 1, 99.0, 'A2', 2);
        INSERT INTO fcyphases VALUES (1, 6, 3, 5, 'SC');
    """)
    db.commit()
    db.close()


def test_compounds_become_race_relative():
    assert tum_timing.relative_compounds("A2,A3,I,W") == {
        "A2": "HARD", "A3": "SOFT", "I": "INTERMEDIATE", "W": "WET"}
    three = tum_timing.relative_compounds("A1,A2,A3")
    assert (three["A1"], three["A2"], three["A3"]) == ("HARD", "MEDIUM", "SOFT")


def test_races_by_date_order_and_drivers_by_surname(tmp_path):
    path = str(tmp_path / "tum.sqlite")
    _database(path)
    overlays = tum_timing.load(path)
    assert set(overlays) == {(2015, 1), (2015, 2)}     # Melbourne first by date
    melbourne = overlays[(2015, 1)]
    assert melbourne.neutral == {3, 4, 5}
    assert overlays[(2015, 2)].compound[("Lewis Hamilton", 1)] == "HARD"

    stored = [RaceLap(season=2015, round=1, driver="Lewis Hamilton", lap=n, lap_time_seconds=95,
                      compound="Unknown", stint=1) for n in (1, 2, 3)]
    stored.append(RaceLap(season=2015, round=1, driver="Nico Hülkenberg", lap=1,
                          lap_time_seconds=101, compound="Unknown", stint=1))
    laps, ages, neutral = tum_timing.apply(stored, melbourne)
    by = {(lap.driver, lap.lap): lap.compound for lap in laps}
    assert by[("Lewis Hamilton", 1)] == "SOFT"
    assert by[("Lewis Hamilton", 2)] == "HARD"
    assert by[("Lewis Hamilton", 3)] == "Unknown"       # not in the overlay: unchanged
    assert by[("Nico Hülkenberg", 1)] == "INTERMEDIATE"  # accents ignored
    assert ages[("Lewis Hamilton", 1)] == 2
    assert neutral == {3, 4, 5}
    assert tum_timing.coverage(laps, ages) == 0.75


def test_no_overlay_changes_nothing():
    laps = [RaceLap(season=2016, round=1, driver="A B", lap=1, lap_time_seconds=90)]
    assert tum_timing.apply(laps, None) == (laps, {}, set())
