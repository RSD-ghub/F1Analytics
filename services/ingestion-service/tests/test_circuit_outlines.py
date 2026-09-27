"""Circuit shapes served for decoration.

Separate from the circuit panel's endpoint on purpose: a watermark has no use
for three hundred speed samples, the corner table or the official record, and
shipping them to draw one would send a couple of hundred kilobytes per page.
"""

import pytest

from app.services.storage import CIRCUITS, IngestionStore


class _Cursor:
    def __init__(self, rows, projection):
        self._rows = rows
        self._projection = projection
        self.limited = None

    def limit(self, n):
        self.limited = n
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        keep = {k for k, v in (self._projection or {}).items() if v}
        async def gen():
            for row in self._rows:
                yield {k: v for k, v in row.items() if not keep or k in keep}
        return gen()


class _Collection:
    def __init__(self, rows):
        self.rows = rows
        self.criteria = None
        self.cursor = None

    def find(self, criteria, projection=None):
        self.criteria = criteria
        self.cursor = _Cursor(list(self.rows), projection)
        return self.cursor


class _Db:
    def __init__(self, rows):
        self.collection = _Collection(rows)

    def __getitem__(self, name):
        assert name == CIRCUITS
        return self.collection


def _circuit(slug, points=3, official=True):
    row = {
        "id": slug, "slug": slug, "circuit": slug.title(),
        "outline": [[float(i), float(i)] for i in range(points)],
        "speeds": [200] * points,
        "corners": [{"number": 1}],
    }
    if official:
        row["official"] = {"length_km": 5.0, "lap_record_time": "1:20.000"}
    return row


async def test_only_the_shape_is_returned():
    """Speeds, corners and the official record are the circuit panel's
    business. A backdrop asks for a line."""
    db = _Db([_circuit("monza")])

    rows = await IngestionStore(db).circuit_outlines()

    assert set(rows[0]) <= {"slug", "circuit", "outline"}
    assert "speeds" not in rows[0]
    assert "official" not in rows[0]


async def test_a_circuit_with_no_geometry_is_not_offered():
    """The collection also holds records that carry official figures and no
    layout — Madrid has never been drawn. Returning one gives the page an
    empty slide to fade to."""
    db = _Db([_circuit("monza")])

    await IngestionStore(db).circuit_outlines()

    assert db.collection.criteria == {"outline.1": {"$exists": True}}


async def test_the_limit_is_bounded_both_ways():
    """A caller asking for a thousand backdrops is asking by mistake."""
    db = _Db([_circuit("a"), _circuit("b")])
    store = IngestionStore(db)

    await store.circuit_outlines(1000)
    assert db.collection.cursor.limited == 30

    await store.circuit_outlines(0)
    assert db.collection.cursor.limited == 1
