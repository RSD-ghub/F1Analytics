"""Filling a finished race's starting grid from the grid we already held.

Upstream does not always publish one. The 2026 Azerbaijan race came back with
``GridPosition`` of -1 for all twenty-two cars, which leaves the race unable to
say who started where — and that is the input to how often pole converts and
how many places the average car changes, the two circuit statistics that are
worth anything.

We hold the answer already: the qualifying rows carry the confirmed grid, read
off the FIA's own document hours before the start.
"""

from app.services.storage import IngestionStore
from app.services.transforms import extract_results
from app.models.schemas import ExpectedSession


class _Collection:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.writes = []

    def find(self, criteria, projection=None):
        return _Cursor([
            row for row in self.rows
            if all(
                _matches(row.get(key), value) for key, value in criteria.items()
            )
        ])

    async def bulk_write(self, operations):
        self.writes.extend(operations)
        for op in operations:
            target = op._filter["_id"]
            update = op._doc["$set"]
            for row in self.rows:
                if row["_id"] == target:
                    row.update(update)


def _matches(actual, expected):
    if isinstance(expected, dict):
        if "$gt" in expected:
            return (actual or 0) > expected["$gt"]
    return actual == expected


class _Db:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row
        return gen()


def _store(results, qualifying):
    return IngestionStore(_Db({
        "results": _Collection(results),
        "qualifying": _Collection(qualifying),
    })), None


def _result(_id, driver, grid=-1):
    return {"_id": _id, "season": 2026, "round": 15, "driver": driver,
            "grid_position": grid}


def _quali(driver, grid):
    return {"season": 2026, "round": 15, "driver": driver, "grid_position": grid}


async def test_a_missing_grid_is_filled_from_the_confirmed_one():
    db = _Db({
        "results": _Collection([_result(1, "Russell"), _result(2, "Sainz")]),
        "qualifying": _Collection([_quali("Russell", 1), _quali("Sainz", 14)]),
    })

    outcome = await IngestionStore(db).backfill_result_grid(2026, 15)

    assert outcome == {"filled": 2, "missing": 0}
    assert [row["grid_position"] for row in db["results"].rows] == [1, 14]


async def test_a_grid_upstream_did_supply_is_left_alone():
    """The race's own grid is the better source when it exists; this only
    fills a hole."""
    db = _Db({
        "results": _Collection([_result(1, "Russell", grid=3)]),
        "qualifying": _Collection([_quali("Russell", 1)]),
    })

    outcome = await IngestionStore(db).backfill_result_grid(2026, 15)

    assert outcome["filled"] == 0
    assert db["results"].rows[0]["grid_position"] == 3


async def test_no_confirmed_grid_means_nothing_is_invented():
    db = _Db({
        "results": _Collection([_result(1, "Russell")]),
        "qualifying": _Collection([]),
    })

    outcome = await IngestionStore(db).backfill_result_grid(2026, 15)

    assert outcome["filled"] == 0
    assert db["results"].rows[0]["grid_position"] == -1


async def test_a_driver_with_no_grid_slot_is_counted_not_guessed():
    db = _Db({
        "results": _Collection([_result(1, "Russell"), _result(2, "Nobody")]),
        "qualifying": _Collection([_quali("Russell", 1)]),
    })

    outcome = await IngestionStore(db).backfill_result_grid(2026, 15)

    assert outcome == {"filled": 1, "missing": 1}


# ── The reading that caused it ───────────────────────────────────────────────


def test_a_negative_grid_reading_is_stored_as_unknown():
    """Zero already means "we do not know". A -1 says the same thing in a way
    that sorts ahead of pole and reads like a measurement."""
    import pandas as pd

    session = ExpectedSession(season=2026, round=15, race_name="GP", circuit="Baku")
    rows = extract_results(session, pd.DataFrame([{
        "Abbreviation": "RUS", "FullName": "George Russell", "TeamName": "Mercedes",
        "Position": 1.0, "ClassifiedPosition": "1", "Points": 25.0,
        "GridPosition": -1.0, "Status": "Finished",
    }]))

    assert rows[0].grid_position == 0
