"""The FIA qualifying classification, used when FastF1 is slow.

FastF1 is preferred but is the slowest source in exactly the window that
matters. For hours after a session it serves an entry list with every position
blank, and the grid-aware lock windows cannot fire without a classification.

Measured before the 2026 Spanish Grand Prix: at 16:58 UTC, three hours after
qualifying ended, FastF1 had 22 entries and no positions, jolpica had nothing at
all, and the FIA had already published document 44. The authoritative source was
also the fastest one.

Lines below are verbatim from that document.
"""

import pytest

from app.services.fia_documents import (
    GridDocumentUnreadable,
    _parse_qualifying_lines,
    _split_name_and_team,
    qualifying_document_url,
)

SPAIN_2026 = [
    "2026 SPANISH GRAND PRIX",
    "From The Stewards Document 44",
    "Title Provisional Qualifying Classification",
    "Doc 44 Time 17:15",
    "NO DRIVER NAT ENTRANT Q1 LAPS % TIME Q2 LAPS TIME Q3 LAPS TIME",
    "1 1 Lando NORRIS McLaren Mastercard F1 Team 1:33.469 7 100.276 16:13:54 1:32.873 6 16:39:49 1:31.824",
    "2 12 Kimi ANTONELLI Mercedes-AMG PETRONAS F1 Team 1:33.267 8 100.060 16:16:33 1:32.591 6 16:38:51 1:31.835",
    "3 3 Max VERSTAPPEN Oracle Red Bull Racing 1:33.381 6 100.182 16:09:56 1:32.431 6 16:39:42 1:31.964",
    "4 44 Lewis HAMILTON Scuderia Ferrari HP 1:33.531 6 100.343 16:10:47 1:32.710 9 16:34:57 1:32.013",
    "5 16 Charles LECLERC Scuderia Ferrari HP 1:33.532 6 100.344 16:10:35 1:32.755 9 16:34:31 1:32.019",
    "6 63 George RUSSELL Mercedes-AMG PETRONAS F1 Team 1:33.211 10 100.000 16:19:32 1:32.850 6 16:28:26 1:32.149",
    "7 81 Oscar PIASTRI McLaren Mastercard F1 Team 1:33.829 10 100.663 16:13:48 1:33.204 6 16:30:08 1:32.294",
    "8 30 Liam LAWSON Oracle Red Bull Racing 1:33.310 6 100.106 16:16:55 1:32.780 6 16:40:13 1:32.316",
    "9 43 Franco COLAPINTO BWT Alpine F1 Team 1:33.963 6 100.806 16:17:45 1:33.038 5 16:39:23 1:32.903",
    "10 41 Arvid LINDBLAD Visa Cash App Racing Bulls F1 Team 1:34.340 9 101.211 16:17:11 1:33.204 6 16:39:00 1:33.100",
    "11 27 Nico HULKENBERG Audi Revolut F1 Team 1:34.417 7 101.293 16:05:02 1:33.223 6 16:39:16",
    "12 5 Gabriel BORTOLETO Audi Revolut F1 Team 1:33.986 6 100.831 16:05:06 1:33.388 6 16:39:30",
    "13 31 Esteban OCON TGR Haas F1 Team 1:34.667 5 101.562 16:08:13 1:33.667 6 16:40:08",
    "14 10 Pierre GASLY BWT Alpine F1 Team 1:34.246 6 101.110 16:17:38 1:33.753 5 16:39:06",
    "15 22 Yuki TSUNODA Visa Cash App Racing Bulls F1 Team 1:34.311 9 101.180 16:17:24 1:34.084 6 16:40:04",
    "16 23 Alexander ALBON Atlassian Williams F1 Team 1:35.307 9 102.248 16:17:05 1:35.532 5 16:29:06",
    "17 55 Carlos SAINZ Atlassian Williams F1 Team 1:35.312 9 102.254 16:16:42",
    "18 14 Fernando ALONSO Aston Martin Aramco F1 Team 1:35.388 9 102.335 16:16:49",
    "19 11 Sergio PEREZ Cadillac Formula 1 Team 1:35.913 9 102.898 16:18:13",
    "20 77 Valtteri BOTTAS Cadillac Formula 1 Team 1:38.011 8 105.149 16:03:30",
    "POLE POSITION",
    "1 Lando NORRIS McLaren Mastercard F1 Team 1:31.824 212.258 KM/H",
    "FASTEST LAP",
    "1 Lando NORRIS McLaren Mastercard F1 Team 1:31.824 212.258 KM/H",
]


def test_the_classification_parses():
    entries, doc = _parse_qualifying_lines(SPAIN_2026)
    assert doc == 44
    assert len(entries) == 20
    assert (entries[0].position, entries[0].car_number) == (1, 1)


def test_lap_times_are_seconds_not_clock_stamps():
    """Each row carries session clock stamps like 16:13:54 alongside the lap
    times. Reading one of those as a lap would give a 16-minute Q1."""
    entries, _ = _parse_qualifying_lines(SPAIN_2026)
    pole = entries[0]

    assert pole.q1_seconds == pytest.approx(93.469)
    assert pole.q2_seconds == pytest.approx(92.873)
    assert pole.q3_seconds == pytest.approx(91.824)


def test_a_driver_eliminated_in_q1_has_no_q2_or_q3():
    entries, _ = _parse_qualifying_lines(SPAIN_2026)
    bottas = next(e for e in entries if e.car_number == 77)

    assert bottas.q1_seconds == pytest.approx(98.011)
    assert bottas.q2_seconds == 0.0
    assert bottas.q3_seconds == 0.0


def test_the_pole_and_fastest_lap_blocks_are_not_read_as_drivers():
    """Both repeat a driver row without a position and would otherwise be
    appended to the classification."""
    entries, _ = _parse_qualifying_lines(SPAIN_2026)
    assert len(entries) == 20
    assert entries[-1].car_number == 77


def test_a_truncated_document_is_rejected():
    with pytest.raises(GridDocumentUnreadable):
        _parse_qualifying_lines(SPAIN_2026[:10])


# ── Name and team separation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,name,team",
    [
        ("Lando NORRIS McLaren Mastercard F1 Team", "Lando NORRIS", "McLaren Mastercard F1 Team"),
        # Teams that themselves begin in capitals are the interesting case: the
        # surname run has to stop at the first token that is not wholly capital.
        ("Esteban OCON TGR Haas F1 Team", "Esteban OCON", "TGR Haas F1 Team"),
        ("Pierre GASLY BWT Alpine F1 Team", "Pierre GASLY", "BWT Alpine F1 Team"),
        ("Lewis HAMILTON Scuderia Ferrari HP", "Lewis HAMILTON", "Scuderia Ferrari HP"),
    ],
)
def test_the_driver_is_separated_from_the_team(raw, name, team):
    assert _split_name_and_team(raw) == (name, team)


def test_the_url_follows_the_same_slug_rule_as_the_grid():
    assert qualifying_document_url(2026, "Spanish Grand Prix", "provisional").endswith(
        "/2026_spanish_grand_prix_-_provisional_qualifying_classification.pdf"
    )


def test_a_two_word_surname_is_a_known_limitation():
    """Documented rather than solved.

    The team follows the surname with no separator and many teams begin with a
    capitalised acronym, so "capitals until they stop" cannot distinguish
    "DE VRIES Aston" from "GASLY BWT". One token is taken. The second word of a
    two-word surname lands at the head of the team string, which is cosmetic:
    every join that matters keys on the car number.
    """
    name, team = _split_name_and_team("Nyck DE VRIES Aston Martin Aramco F1 Team")
    assert name == "Nyck DE"
    assert team.startswith("VRIES")
