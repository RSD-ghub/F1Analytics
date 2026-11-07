"""Placing circuits on the globe.

Melbourne has no match in OpenStreetMap under its official name, and the
near-miss names land in Brisbane or 30 km from the track, so it is placed
by hand and never searched.
"""

from app.routers import circuits


class _Store:
    def __init__(self, weekends):
        self.weekends = weekends
        self.saved = {}

    async def list_weekends(self, first, last):
        return self.weekends

    async def circuit_map(self, name):
        return {"official": {"official_name": name + " Official"}}

    async def save_circuit_location(self, key, lat, lon):
        self.saved[key] = (lat, lon)


async def test_melbourne_is_placed_without_a_search(monkeypatch):
    searched = []

    def locate(query):
        searched.append(query)
        return 31.34, 121.22

    monkeypatch.setattr(circuits.osm_circuit, "locate", locate)
    monkeypatch.setattr(circuits.asyncio, "sleep", _no_sleep)
    store = _Store([{"round": 1, "circuit": "Melbourne"}, {"round": 2, "circuit": "Shanghai"}])

    out = await circuits.refresh_locations(2026, store=store)

    assert store.saved["melbourne"] == (-37.8497, 144.9680)
    assert searched == ["Shanghai Official"]
    assert out["failed"] == []


async def _no_sleep(_):
    return None
