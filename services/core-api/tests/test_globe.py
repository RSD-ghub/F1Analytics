"""The globe lists the whole season, even rounds it cannot place on a map.

The Home page counts "N of M rounds have been run" from this list. When a
circuit had no coordinates it used to be left out, so one ungeocoded venue
made the page undercount both numbers (18 of 22 when 19 of 23 had run).
"""

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.routers import dashboard


class _Client:
    def __init__(self, routes):
        self.routes = routes

    async def get(self, path, **_):
        return self.routes[path]


def _iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def test_an_unplaced_circuit_is_listed_without_coordinates(monkeypatch):
    season = datetime.now(timezone.utc).year
    routes = {
        "/forward/calendar/{}".format(season): [
            {"round": 1, "race_name": "Raced, placed", "race_start_utc": _iso(-30)},
            {"round": 2, "race_name": "Raced, not geocoded", "race_start_utc": _iso(-20)},
            {"round": 3, "race_name": "Next", "race_start_utc": _iso(5)},
        ],
        "/forward/next": {"round": 3},
        "/circuits/{}/1".format(season): {"location": {"lat": 26.03, "lon": 50.51}},
        "/circuits/{}/2".format(season): {"location": {}},
        "/circuits/{}/3".format(season): {"location": {"lat": 0.0, "lon": -23.5}},
    }
    monkeypatch.setattr(dashboard, "_clients", lambda settings: {"ingestion": _Client(routes)})

    stops = await dashboard.globe(Settings())

    assert [s.round for s in stops] == [1, 2, 3]
    assert [s.status for s in stops] == ["raced", "raced", "next"]
    unplaced = stops[1]
    assert unplaced.lat is None and unplaced.lon is None
    # A real latitude of zero is a place, not a missing one.
    assert stops[2].lat == 0.0
