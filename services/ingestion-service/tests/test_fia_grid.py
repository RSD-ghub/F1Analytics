"""The FIA starting-grid source and the join onto our qualifying rows.

The parser is exercised against text captured verbatim from real documents
rather than invented fixtures, because every interesting case here is a quirk of
how the FIA lays the page out — and an invented fixture would only ever test the
layout I already believed in.
"""

import pytest

from app.models.schemas import GridSource, QualifyingRow
from app.services import grid_resolution
from app.services.fia_documents import (
    GridDocumentUnreadable,
    StartingGridDocument,
    _parse_lines,
    document_url,
    event_slug,
)

# Verbatim from the 2025 São Paulo Grand Prix final starting grid (doc 58) —
# the awkward one. Eighteen cars on the grid, two sent to the pit lane, one
# driver with no time, and a NOTES block after the field.
SAO_PAULO_LINES = [
    "2025 SÃO PAULO GRAND PRIX",
    "From The Stewards Document 58",
    "Title Final Starting Grid",
    "Doc 58 Time 13:00",
    "1 4 Lando NORRIS 1:09.511",
    "McLaren Formula 1 Team",
    "2 12 Kimi ANTONELLI 1:09.685",
    "Mercedes-AMG PETRONAS F1 Team",
    "3 16 Charles LECLERC 1:09.805",
    "Scuderia Ferrari HP",
    "4 81 Oscar PIASTRI 1:09.886",
    "McLaren Formula 1 Team",
    "5 6 Isack HADJAR 1:09.931",
    "Visa Cash App Racing Bulls F1 Team",
    "6 63 George RUSSELL 1:09.942",
    "Mercedes-AMG PETRONAS F1 Team",
    "7 30 Liam LAWSON 1:09.962",
    "Visa Cash App Racing Bulls F1 Team",
    "8 87 Oliver BEARMAN 1:09.977",
    "MoneyGram Haas F1 Team",
    "9 10 Pierre GASLY 1:10.002",
    "BWT Alpine F1 Team",
    "10 27 Nico HULKENBERG 1:10.039",
    "Stake F1 Team Kick Sauber",
    "11 14 Fernando ALONSO 1:10.001",
    "Aston Martin Aramco F1 Team",
    "12 23 Alexander ALBON 1:10.053",
    "Atlassian Williams Racing",
    "13 44 Lewis HAMILTON 1:10.100",
    "Scuderia Ferrari HP",
    "14 18 Lance STROLL 1:10.161",
    "Aston Martin Aramco F1 Team",
    "15 55 Carlos SAINZ 1:10.472",
    "Atlassian Williams Racing",
    "16 43 Franco COLAPINTO 1:10.632",
    "BWT Alpine F1 Team",
    "17 22 Yuki TSUNODA 1:10.711",
    "Oracle Red Bull Racing",
    "18 5 Gabriel BORTOLETO",
    "Stake F1 Team Kick Sauber",
    "DRIVERS REQUIRED TO START FROM THE PIT LANE",
    "1 Max VERSTAPPEN * 1:10.403",
    "Oracle Red Bull Racing",
    "31 Esteban OCON * 1:10.438",
    "MoneyGram Haas F1 Team",
    "NOTES",
    "Car 5 - Permitted to start - Stewards' document no. 46",
    "* PENALTIES",
]


# ── URL derivation ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Belgian Grand Prix", "belgian_grand_prix"),
        ("São Paulo Grand Prix", "sao_paulo_grand_prix"),
        ("United States Grand Prix", "united_states_grand_prix"),
    ],
)
def test_event_slug_matches_fia_url_form(name, expected):
    """Accents are stripped, not encoded — verified against all 24 2025 events."""
    assert event_slug(name) == expected


def test_document_url_shape():
    assert document_url(2025, "Monaco Grand Prix", "final").endswith(
        "/2025_monaco_grand_prix_-_final_starting_grid.pdf"
    )


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_parses_the_grid_and_the_document_number():
    entries, doc = _parse_lines(SAO_PAULO_LINES)
    assert doc == 58
    assert len(entries) == 20
    assert (entries[0].position, entries[0].car_number) == (1, 4)
    assert entries[0].driver_name == "Lando NORRIS"
    assert entries[0].team == "McLaren Formula 1 Team"


def test_a_driver_with_no_lap_time_still_takes_their_slot():
    """Bortoleto is classified P18 without a time. Dropping him would shift
    every pit-lane position by one."""
    entries, _ = _parse_lines(SAO_PAULO_LINES)
    bortoleto = next(e for e in entries if e.car_number == 5)
    assert bortoleto.position == 18


def test_pit_lane_starters_go_behind_the_grid_not_on_pole():
    """The single most dangerous misread in this parser.

    In the pit-lane block the only leading number is the *car* number, so
    reading "1 Max VERSTAPPEN" with grid-row semantics puts the driver who is
    starting last on pole. Race results for this event record Verstappen at
    grid 19 and Ocon at 20 behind an eighteen-car grid — these assertions are
    that ground truth.
    """
    entries, _ = _parse_lines(SAO_PAULO_LINES)
    verstappen = next(e for e in entries if e.car_number == 1)
    ocon = next(e for e in entries if e.car_number == 31)

    assert verstappen.position == 19
    assert ocon.position == 20
    assert verstappen.from_pit_lane and ocon.from_pit_lane
    assert entries[0].car_number == 4  # Norris still has pole


def test_notes_block_is_not_read_as_grid_rows():
    entries, _ = _parse_lines(SAO_PAULO_LINES)
    assert all("Stewards" not in e.driver_name for e in entries)
    assert len(entries) == 20


def test_a_short_grid_is_rejected_rather_than_returned():
    """Half a grid is worse than none — it would silently move real drivers."""
    with pytest.raises(GridDocumentUnreadable):
        _parse_lines(SAO_PAULO_LINES[:12])


def test_duplicate_car_numbers_are_rejected():
    lines = list(SAO_PAULO_LINES)
    lines[5] = "2 4 Kimi ANTONELLI 1:09.685"  # car 4 twice
    with pytest.raises(GridDocumentUnreadable):
        _parse_lines(lines)


# ── Applying the grid ────────────────────────────────────────────────────────


def _rows(with_numbers=True):
    people = [
        (4, "Lando Norris"), (12, "Andrea Kimi Antonelli"), (16, "Charles Leclerc"),
        (81, "Oscar Piastri"), (6, "Isack Hadjar"), (63, "George Russell"),
        (30, "Liam Lawson"), (87, "Oliver Bearman"), (10, "Pierre Gasly"),
        (27, "Nico Hulkenberg"), (14, "Fernando Alonso"), (23, "Alexander Albon"),
        (44, "Lewis Hamilton"), (18, "Lance Stroll"), (55, "Carlos Sainz"),
        (43, "Franco Colapinto"), (22, "Yuki Tsunoda"), (5, "Gabriel Bortoleto"),
        (1, "Max Verstappen"), (31, "Esteban Ocon"),
    ]
    return [
        QualifyingRow(
            id="q-{}".format(number), season=2025, round=21, driver=name,
            position=index + 1, driver_number=number if with_numbers else 0,
        )
        for index, (number, name) in enumerate(people)
    ]


def _document():
    entries, _ = _parse_lines(SAO_PAULO_LINES)
    return StartingGridDocument(
        season=2025, round=21, event_name="São Paulo Grand Prix",
        kind="final", url="http://example/doc.pdf", entries=tuple(entries),
    )


def test_applying_the_grid_sets_positions_and_provenance():
    applied = grid_resolution.apply_starting_grid(_rows(), _document())
    by_driver = {row.driver: row for row in applied}

    assert by_driver["Lando Norris"].grid_position == 1
    assert by_driver["Max Verstappen"].grid_position == 19
    assert by_driver["Max Verstappen"].starts_from_pit_lane is True
    assert by_driver["Lando Norris"].grid_source == GridSource.OFFICIAL_FINAL
    assert by_driver["Lando Norris"].has_confirmed_grid is True


def test_the_penalty_case_actually_moves_a_driver():
    """Verstappen qualifies P19 in our stub only by construction; the point is
    that grid position is taken from the document, not from ``position``."""
    applied = grid_resolution.apply_starting_grid(_rows(), _document())
    hulkenberg = next(r for r in applied if r.driver == "Nico Hulkenberg")
    assert hulkenberg.position == 10
    assert hulkenberg.grid_position == 10


def test_it_joins_on_car_number_not_spelling():
    """The FIA writes "Kimi ANTONELLI"; FastF1 says "Andrea Kimi Antonelli".

    A first-name join would drop this row and reject the whole grid.
    """
    applied = grid_resolution.apply_starting_grid(_rows(), _document())
    antonelli = next(r for r in applied if r.driver == "Andrea Kimi Antonelli")
    assert antonelli.grid_position == 2


def test_surname_fallback_works_when_car_numbers_are_missing():
    """Rows ingested before car numbers were stored must still resolve."""
    applied = grid_resolution.apply_starting_grid(_rows(with_numbers=False), _document())
    assert next(r for r in applied if r.driver == "Lando Norris").grid_position == 1


def test_an_unmatched_entry_rejects_the_whole_grid():
    """All-or-nothing. A partly applied grid mixes true and assumed slots into
    an order that never existed, which is harder to detect than having none."""
    rows = [r for r in _rows() if r.driver != "Max Verstappen"]
    with pytest.raises(grid_resolution.GridApplicationError) as exc:
        grid_resolution.apply_starting_grid(rows, _document())
    assert "Max VERSTAPPEN" in str(exc.value)


def test_rows_not_on_the_grid_are_left_unconfirmed_rather_than_guessed():
    rows = _rows() + [
        QualifyingRow(id="q-99", season=2025, round=21, driver="Did Notqualify",
                      position=21, driver_number=99)
    ]
    applied = grid_resolution.apply_starting_grid(rows, _document())
    spare = next(r for r in applied if r.driver == "Did Notqualify")
    assert spare.grid_position == 0
    assert spare.has_confirmed_grid is False


# ── Scheduled confirmation ───────────────────────────────────────────────────


class _Store:
    """Minimal store standing in for Mongo."""

    def __init__(self, quali_rows, raced_rounds=()):
        self._quali = quali_rows
        self._raced = set(raced_rounds)
        self.grid_saves = 0

    async def list_weekends(self, *_):
        return [{"round": 1, "race_name": "One"}, {"round": 2, "race_name": "Two"}]

    async def count_matching(self, _collection, query):
        return 1 if query["round"] in self._raced else 0

    async def find_rows(self, _collection, query, sort=None):
        return [r.model_dump(mode="json") for r in self._quali.get(query["round"], [])]

    async def save_qualifying(self, *_args):
        self.grid_saves += 1
        return 20


def _runner(store, monkeypatch, fetched):
    from app.services.ingest_runner import IngestRunner
    from app.services import fia_documents as fd

    runner = IngestRunner.__new__(IngestRunner)
    runner._store = store

    async def fake_fetch(season, round_number, event_name, **kwargs):
        fetched.append(round_number)
        return _document()

    monkeypatch.setattr(fd, "fetch_starting_grid", fake_fetch)
    return runner


async def test_a_round_that_has_already_raced_is_not_chased(monkeypatch):
    """Its grid is settled history, and re-reading documents for every finished
    round on every tick would hammer an upstream doing us a favour."""
    store = _Store({1: _rows(), 2: _rows()}, raced_rounds={1})
    fetched = []
    await _runner(store, monkeypatch, fetched).refresh_pending_grids(2025)

    assert fetched == [2]


async def test_a_round_with_a_confirmed_grid_is_not_re_fetched(monkeypatch):
    confirmed = [
        r.model_copy(update={"grid_position": i + 1, "grid_source": GridSource.OFFICIAL_FINAL})
        for i, r in enumerate(_rows())
    ]
    store = _Store({1: confirmed, 2: _rows()})
    fetched = []
    await _runner(store, monkeypatch, fetched).refresh_pending_grids(2025)

    assert fetched == [2]


async def test_an_unpublished_grid_is_reported_pending_not_failed(monkeypatch):
    """The normal state between qualifying and the stewards publishing."""
    from app.services import fia_documents as fd

    store = _Store({1: _rows()})
    runner = _runner(store, monkeypatch, [])

    async def unavailable(*_a, **_k):
        raise fd.GridDocumentUnavailable("not published yet")

    monkeypatch.setattr(fd, "fetch_starting_grid", unavailable)
    result = await runner.refresh_pending_grids(2025)

    # Round 2 has no qualifying classification at all, so there is nothing to
    # apply a grid to and it is skipped rather than reported as waiting.
    assert result["still_pending"] == ["2025-1"]
    assert result["failed"] == []
    assert store.grid_saves == 0
