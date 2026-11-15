"""One team, one name, whichever source the qualifying came from."""

import asyncio

from app.models.schemas import QualifyingRow
from app.services.storage import QUALIFYING, IngestionStore


class _Collection:
    def __init__(self):
        self.docs = []

    async def bulk_write(self, operations, ordered=False):
        self.docs += [op._doc for op in operations if hasattr(op, "_doc")]


class _Db(dict):
    pass


def save(season, team):
    collection = _Collection()
    store = IngestionStore(_Db({QUALIFYING: collection}))
    row = QualifyingRow(id="x", season=season, round=19, driver="Max Verstappen", team=team)
    asyncio.run(store.save_qualifying(season, 19, [row]))
    return collection.docs[0]["team"]


def test_jolpicas_spelling_is_stored_as_the_seasons_own():
    assert save(2026, "Red Bull") == "Red Bull Racing"
    assert save(2026, "RB F1 Team") == "Racing Bulls"
    assert save(2026, "McLaren") == "McLaren"


def test_older_seasons_keep_the_period_name():
    assert save(2010, "Red Bull") == "Red Bull"
