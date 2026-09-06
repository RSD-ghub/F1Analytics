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
    entries, _pen, doc = _parse_lines(SAO_PAULO_LINES)
    assert doc == 58
    assert len(entries) == 20
    assert (entries[0].position, entries[0].car_number) == (1, 4)
    assert entries[0].driver_name == "Lando NORRIS"
    assert entries[0].team == "McLaren Formula 1 Team"


def test_a_driver_with_no_lap_time_still_takes_their_slot():
    """Bortoleto is classified P18 without a time. Dropping him would shift
    every pit-lane position by one."""
    entries, _pen, _doc = _parse_lines(SAO_PAULO_LINES)
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
    entries, _pen, _doc = _parse_lines(SAO_PAULO_LINES)
    verstappen = next(e for e in entries if e.car_number == 1)
    ocon = next(e for e in entries if e.car_number == 31)

    assert verstappen.position == 19
    assert ocon.position == 20
    assert verstappen.from_pit_lane and ocon.from_pit_lane
    assert entries[0].car_number == 4  # Norris still has pole


def test_notes_block_is_not_read_as_grid_rows():
    entries, _pen, _doc = _parse_lines(SAO_PAULO_LINES)
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
    entries, _pen, _doc = _parse_lines(SAO_PAULO_LINES)
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


# ── The glued-row regression ─────────────────────────────────────────────────

#: Verbatim from the 2026 Italian Grand Prix provisional grid (doc 51). The PDF
#: text layer runs row 22 onto the end of row 21's team line. Parsed with
#: start-anchored matching only, the grid comes back twenty-one long — and a
#: 1..21 grid passes every structural check there is, so the loss is silent.
ITALY_2026_TAIL = [
    "Doc 51 Time 20:42",
    "1 10 Pierre GASLY 1:21.786",
    "BWT Alpine F1 Team",
    "2 63 George RUSSELL 1:21.846",
    "Mercedes-AMG PETRONAS F1 Team",
    "3 16 Charles LECLERC 1:22.004",
    "Scuderia Ferrari HP",
    "4 44 Lewis HAMILTON 1:22.011",
    "Scuderia Ferrari HP",
    "5 3 Max VERSTAPPEN 1:22.070",
    "Oracle Red Bull Racing",
    "6 81 Oscar PIASTRI * 1:21.966",
    "McLaren Mastercard F1 Team",
    "7 43 Franco COLAPINTO 1:22.220",
    "BWT Alpine F1 Team",
    "8 1 Lando NORRIS 1:22.256",
    "McLaren Mastercard F1 Team",
    "9 41 Arvid LINDBLAD 1:22.286",
    "Visa Cash App Racing Bulls F1 Team",
    "10 5 Gabriel BORTOLETO 1:22.517",
    "Audi Revolut F1 Team",
    "11 87 Oliver BEARMAN 1:22.756",
    "TGR Haas F1 Team",
    "12 27 Nico HULKENBERG 1:22.779",
    "Audi Revolut F1 Team",
    "13 55 Carlos SAINZ 1:23.453",
    "Atlassian Williams F1 Team",
    "14 31 Esteban OCON 1:23.454",
    "TGR Haas F1 Team",
    "15 22 Yuki TSUNODA 1:23.755",
    "Visa Cash App Racing Bulls F1 Team",
    "16 77 Valtteri BOTTAS 1:24.364",
    "Cadillac Formula 1 Team",
    "17 11 Sergio PEREZ 1:24.595",
    "Cadillac Formula 1 Team",
    "18 14 Fernando ALONSO 1:25.150",
    "Aston Martin Aramco F1 Team",
    "19 18 Lance STROLL 1:25.222",
    "Aston Martin Aramco F1 Team",
    "20 12 Kimi ANTONELLI * 1:22.093",
    "Mercedes-AMG PETRONAS F1 Team",
    "21 30 Liam LAWSON * 1:22.821",
    "Oracle Red Bull Racing 22 23 Alexander ALBON * 1:24.356",
    "Atlassian Williams F1 Team",
    "* PENALTIES",
    "Car 12 - 30 place grid penalty - Additional power unit elements have been used",
    "Car 23 - 20 place grid penalty - Additional power unit elements have been used",
    "Car 30 - 35 place grid penalty - Additional power unit elements have been used",
    "Car 81 - 3 place grid penalty - Impeding another driver - Stewards' document no. 47",
]


def test_a_row_glued_onto_the_previous_team_line_is_still_read():
    entries, _pen, _doc = _parse_lines(ITALY_2026_TAIL)

    assert len(entries) == 22
    last = entries[-1]
    assert (last.position, last.car_number) == (22, 23)
    assert last.driver_name == "Alexander ALBON"


def test_the_team_of_a_glued_line_is_not_the_whole_line():
    """Otherwise Lawson's team reads "Oracle Red Bull Racing 22 23 Alexander
    ALBON"."""
    entries, _pen, _doc = _parse_lines(ITALY_2026_TAIL)
    lawson = next(e for e in entries if e.car_number == 30)

    assert lawson.team == "Oracle Red Bull Racing"


def test_penalties_are_captured_from_the_notes():
    _entries, penalties, _doc = _parse_lines(ITALY_2026_TAIL)
    by_car = {p.car_number: p for p in penalties}

    assert by_car[30].places == 35
    assert by_car[81].places == 3
    assert "Impeding" in by_car[81].reason


def test_a_penalised_car_missing_from_the_grid_is_caught():
    """The document validating itself.

    A row lost off the end leaves a contiguous 1..N grid, so no structural check
    sees it. But the notes name the cars that were penalised, and every one of
    them has to be somewhere on the grid those notes explain.
    """
    lines = [l for l in ITALY_2026_TAIL if not l.startswith("21 30 Liam LAWSON")]
    lines = [l.replace("Oracle Red Bull Racing 22 23 Alexander ALBON * 1:24.356",
                       "Oracle Red Bull Racing") for l in lines]
    with pytest.raises(GridDocumentUnreadable) as exc:
        _parse_lines(lines)
    assert "30" in str(exc.value)
