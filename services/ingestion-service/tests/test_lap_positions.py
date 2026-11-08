"""Filling in lap positions without rewriting a race.

A ``--refresh`` replaces a race's rows with whatever the source returns now:
fewer laps (a car out on lap 1 that FastF1 has started dropping) or none (Monza
2018, which FastF1 can no longer load) and stored laps are lost. The positions
backfill only sets ``position`` on laps already stored.
"""

import asyncio

import pandas as pd

from app.models.schemas import ExpectedSession
from app.services import historical_laps
from app.services.fastf1_source import SessionFetchError, SessionFrames
from app.services.ingest_runner import IngestRunner

RACE = ExpectedSession(season=2018, round=14, race_name="Italian Grand Prix",
                       race_date="2018-09-02")


class Store:
    def __init__(self, stored):
        self.stored = stored          # [{"driver", "lap"}]
        self.written = {}

    async def find_rows(self, collection, query=None, limit=0, sort=None):
        return list(self.stored)

    async def set_lap_positions(self, season, round_number, positions):
        self.written = dict(positions)
        keys = {(r["driver"], r["lap"]) for r in self.stored}
        return sum(1 for k in positions if k in keys)


class Source:
    def __init__(self, outcome):
        self.outcome = outcome

    def load_race(self, expected):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _frames(rows):
    results = pd.DataFrame([{"Abbreviation": "VET", "FullName": "Sebastian Vettel",
                             "TeamName": "Ferrari", "Position": 1.0,
                             "ClassifiedPosition": "1", "Points": 25.0,
                             "Status": "Finished"}])
    laps = pd.DataFrame([{"Driver": "VET", "LapNumber": lap, "LapTime": pd.Timedelta(seconds=82),
                          "Compound": "SOFT", "Stint": 1, "Position": place}
                         for lap, place in rows])
    return SessionFrames(session=RACE, results=results, laps=laps)


def _runner(source, store):
    async def no_sleep(_):
        return None
    return IngestRunner(source=source, store=store, max_attempts=1, sleep=no_sleep)


def test_official_positions_are_set_on_stored_laps_only():
    store = Store([{"driver": "Sebastian Vettel", "lap": 1}, {"driver": "Sebastian Vettel", "lap": 2}])
    runner = _runner(Source(_frames([(1, 2.0), (2, 1.0)])), store)
    outcome = asyncio.run(runner.fill_lap_positions(RACE))
    assert outcome == {"source": "official", "positions": 2, "matched": 2}
    assert store.written == {("Sebastian Vettel", 1): 2, ("Sebastian Vettel", 2): 1}


def test_a_source_that_fails_falls_back_to_jolpica(monkeypatch):
    store = Store([{"driver": "Sebastian Vettel", "lap": 1}])
    seen = {}

    async def fake_positions(client, season, round_number, drivers):
        seen["drivers"] = drivers
        return {("Sebastian Vettel", 1): 3}

    monkeypatch.setattr(historical_laps, "lap_positions", fake_positions)
    runner = _runner(Source(SessionFetchError("no _laps")), store)
    outcome = asyncio.run(runner.fill_lap_positions(RACE, jolpica=object()))
    assert outcome == {"source": "jolpica", "positions": 1, "matched": 1}
    assert seen["drivers"] == ["Sebastian Vettel"]


def test_a_failure_with_no_fallback_writes_nothing():
    store = Store([{"driver": "Sebastian Vettel", "lap": 1}])
    runner = _runner(Source(SessionFetchError("no _laps")), store)
    outcome = asyncio.run(runner.fill_lap_positions(RACE))
    assert outcome == {"source": "none", "positions": 0, "matched": 0}
    assert store.written == {}


def test_jolpica_drivers_are_matched_by_surname():
    class Client:
        async def get(self, path, **params):
            if path.endswith("drivers.json"):
                return {"DriverTable": {"Drivers": [
                    {"driverId": "antonelli", "givenName": "Andrea Kimi", "familyName": "Antonelli"},
                    {"driverId": "hulkenberg", "givenName": "Nico", "familyName": "Hülkenberg"},
                    {"driverId": "max_verstappen", "givenName": "Max", "familyName": "Verstappen"},
                ]}}
            return {"total": 1, "RaceTable": {"Races": [{"raceName": "X", "Laps": [
                {"number": "1", "Timings": [
                    {"driverId": "antonelli", "position": "1", "time": "1:30.000"},
                    {"driverId": "hulkenberg", "position": "2", "time": "1:30.500"},
                    {"driverId": "max_verstappen", "position": "3", "time": "1:31.000"},
                ]}]}]}}

    stored = ["Kimi Antonelli", "Nico Hulkenberg", "Max Verstappen", "Jos Verstappen"]
    out = asyncio.run(historical_laps.lap_positions(Client(), 2026, 2, stored))
    # Two stored Verstappens: ambiguous, left out rather than guessed.
    assert out == {("Kimi Antonelli", 1): 1, ("Nico Hulkenberg", 1): 2}
