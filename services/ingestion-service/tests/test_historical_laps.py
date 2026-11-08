"""Pre-2018 laps from jolpica, in ingestion's own row shape."""

from app.services import historical_laps
from app.services.transforms import stable_id

NAMES = {"vettel": "Sebastian Vettel", "alonso": "Fernando Alonso"}


def test_stints_are_counted_from_the_stops():
    timings = [("vettel", lap, 95.0) for lap in range(1, 7)]
    stops = [("vettel", 1, 3, 22.5)]
    laps, pits = historical_laps.build_rows(2013, 1, "Australian Grand Prix", timings, stops, NAMES)
    # Lap 3 is the in-lap, the last of stint 1; lap 4 starts stint 2.
    assert [lap.stint for lap in laps] == [1, 1, 1, 2, 2, 2]
    assert pits[0].driver == "Sebastian Vettel" and pits[0].lap == 3
    assert pits[0].duration_seconds == 22.5


def test_before_pit_data_the_stint_is_unknown_not_one():
    laps, pits = historical_laps.build_rows(
        2010, 1, "Bahrain Grand Prix", [("alonso", 1, 120.0), ("alonso", 2, 98.0)], [], NAMES)
    assert {lap.stint for lap in laps} == {0} and pits == []
    assert {lap.compound for lap in laps} == {"Unknown"}


def test_rows_share_fastf1_ids_and_unmapped_drivers_are_dropped():
    laps, _ = historical_laps.build_rows(
        2015, 4, "Bahrain Grand Prix", [("vettel", 7, 99.0), ("nobody", 7, 99.5)], [], NAMES)
    assert len(laps) == 1
    assert laps[0].id == stable_id(2015, 4, "Sebastian Vettel", 7)


def test_the_place_at_the_end_of_each_lap_is_kept():
    laps, _ = historical_laps.build_rows(
        2015, 4, "Bahrain Grand Prix", [("vettel", 7, 99.0, 3), ("alonso", 7, 99.5, 0)], [], NAMES)
    assert {lap.driver: lap.position for lap in laps} == {
        "Sebastian Vettel": 3, "Fernando Alonso": 0}


def test_jolpica_positions_are_read(monkeypatch):
    import asyncio

    class Client:
        async def get(self, path, **params):
            return {"total": 1, "RaceTable": {"Races": [{"raceName": "Bahrain Grand Prix", "Laps": [
                {"number": "1", "Timings": [{"driverId": "vettel", "position": "2", "time": "1:39.000"}]}]}]}}

    name, rows = asyncio.run(historical_laps.race_timings(Client(), 2015, 4))
    assert rows == [("vettel", 1, 99.0, 2)]
