"""One driver, one name, whichever backend FastF1 read him from.

The 2026 United States Grand Prix is the case. F1's live timing names car 12
"Kimi Antonelli", and every earlier 2026 row is filed under that. Live timing
did not load for Austin, FastF1 fell back to jolpica, and jolpica names him
"Andrea Kimi Antonelli". The result was stored under the second spelling, so
the race he won was scored as one our favourite never started, and the front
page said we had called "Kimi Antonelli" when it went to "Andrea Kimi
Antonelli" instead.
"""

import pandas as pd

from app.models.schemas import ExpectedSession
from app.services.fastf1_source import (
    FastF1Source,
    SessionFrames,
    canonical_driver_names,
)
from app.services.ingest_runner import IngestRunner, build_payload
from app.services.storage import IngestionStore

AUSTIN = ExpectedSession(
    season=2026, round=18, race_name="United States Grand Prix",
    circuit="Austin", race_date="2026-10-25",
)

REGISTRY = {12: "Kimi Antonelli", 16: "Charles Leclerc", 11: "Sergio Perez"}


def _jolpica_frame():
    """Austin's podium as FastF1 returns it when jolpica answered."""
    return pd.DataFrame([
        {"DriverNumber": "12", "FullName": "Andrea Kimi Antonelli",
         "Abbreviation": "ANT", "TeamName": "Mercedes", "Position": 1.0},
        {"DriverNumber": "16", "FullName": "Charles Leclerc",
         "Abbreviation": "LEC", "TeamName": "Ferrari", "Position": 2.0},
        {"DriverNumber": "11", "FullName": "Sergio Pérez",
         "Abbreviation": "PER", "TeamName": "Cadillac", "Position": 3.0},
        {"DriverNumber": "41", "FullName": "Arvid Lindblad",
         "Abbreviation": "LIN", "TeamName": "Racing Bulls", "Position": 4.0},
    ])


class _Session:
    @property
    def results(self):
        return _jolpica_frame()


def test_the_car_number_decides_the_name():
    frame = canonical_driver_names(_jolpica_frame(), REGISTRY)

    assert list(frame["FullName"]) == [
        "Kimi Antonelli", "Charles Leclerc", "Sergio Perez",
        # Not in the registry: a new driver keeps upstream's name.
        "Arvid Lindblad",
    ]


def test_the_input_frame_is_left_alone():
    original = _jolpica_frame()
    canonical_driver_names(original, REGISTRY)
    assert original.loc[0, "FullName"] == "Andrea Kimi Antonelli"


def test_no_registry_changes_nothing():
    frame = _jolpica_frame()
    assert canonical_driver_names(frame, {}) is frame
    assert canonical_driver_names(frame, None) is frame
    empty = pd.DataFrame()
    assert canonical_driver_names(empty, REGISTRY) is empty


def test_austin_is_stored_under_the_name_the_forecast_used():
    """End to end through the payload the runner saves."""
    source = FastF1Source(cache_dir="unused")
    source.use_driver_names(2026, REGISTRY)

    frames = SessionFrames(
        session=AUSTIN, results=source._results_frame(_Session(), 2026)
    )
    payload = build_payload(frames)

    assert payload.results[0].driver == "Kimi Antonelli"
    assert payload.results[0].position == 1


def test_names_are_per_season():
    source = FastF1Source(cache_dir="unused")
    source.use_driver_names(2026, REGISTRY)

    frame = source._results_frame(_Session(), 2025)
    assert frame.loc[0, "FullName"] == "Andrea Kimi Antonelli"


# ── The runner primes the source from the registry ──────────────────────────


class _PrimedSource:
    def __init__(self):
        self.names = {}

    def use_driver_names(self, season, names):
        self.names[season] = names


def _runner(source, store):
    runner = IngestRunner.__new__(IngestRunner)
    runner._source = source
    runner._store = store
    return runner


async def test_runner_hands_the_registry_to_the_source():
    class _Store:
        async def recall_drivers(self, season):
            return {12: ("Kimi Antonelli", "Mercedes")}

    source = _PrimedSource()
    await _runner(source, _Store())._prime_driver_names(2026)

    assert source.names == {2026: {12: "Kimi Antonelli"}}


async def test_a_registry_outage_does_not_stop_ingestion():
    class _Store:
        async def recall_drivers(self, season):
            raise RuntimeError("mongo down")

    source = _PrimedSource()
    await _runner(source, _Store())._prime_driver_names(2026)
    assert source.names == {}


async def test_a_source_without_priming_is_fine():
    class _Store:
        async def recall_drivers(self, season):
            raise AssertionError("should not be asked")

    await _runner(object(), _Store())._prime_driver_names(2026)


# ── The registry keeps the first spelling ───────────────────────────────────


async def test_registry_never_renames_a_driver():
    """An entry list read through jolpica must not rename car 12 mid-season."""
    written = []

    class _Collection:
        async def bulk_write(self, operations, ordered=True):
            written.extend(operations)

            class _Result:
                upserted_count = 0
                modified_count = len(operations)

            return _Result()

    class _Db:
        def __getitem__(self, name):
            return _Collection()

    store = IngestionStore(_Db())
    await store.remember_drivers(
        2026, {12: ("Andrea Kimi Antonelli", "Mercedes")}
    )

    update = written[0]._doc
    assert "driver" not in update["$set"]
    assert update["$setOnInsert"] == {"driver": "Andrea Kimi Antonelli"}
    assert update["$set"]["team"] == "Mercedes"
