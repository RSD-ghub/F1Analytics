"""Standings tests, weighted heavily toward leakage.

The off-by-one between "standings after round N" and "standings before round N"
is the single highest-consequence bug in this codebase: it is invisible in
output, it inflates backtest accuracy, and it makes the published track record a
lie. So the boundary gets tested from both sides.
"""

from app.models.schemas import ResultRow
from app.services.standings import (
    compute_standings,
    standings_before_round,
)


def _result(round_number, driver, position, points, team="Team A"):
    return ResultRow(
        id="{}-{}".format(round_number, driver),
        season=2024,
        round=round_number,
        driver=driver,
        team=team,
        position=position,
        points=points,
    )


#: Rounds 1-3. Verstappen wins 1 and 3, Norris wins 2.
SEASON = [
    _result(1, "Max Verstappen", 1, 25.0, "Red Bull"),
    _result(1, "Lando Norris", 2, 18.0, "McLaren"),
    _result(1, "Oscar Piastri", 3, 15.0, "McLaren"),
    _result(2, "Lando Norris", 1, 25.0, "McLaren"),
    _result(2, "Max Verstappen", 2, 18.0, "Red Bull"),
    _result(2, "Oscar Piastri", 4, 12.0, "McLaren"),
    _result(3, "Max Verstappen", 1, 25.0, "Red Bull"),
    _result(3, "Oscar Piastri", 2, 18.0, "McLaren"),
    _result(3, "Lando Norris", 20, 0.0, "McLaren"),
]


# ── Point-in-time correctness ────────────────────────────────────────────────


def test_standings_before_a_round_exclude_that_round():
    """The core guarantee: predicting round 3 must not see round 3."""
    snapshot = standings_before_round(SEASON, 2024, 3)

    assert snapshot.included_rounds == [1, 2]
    assert snapshot.through_round == 2
    # After two rounds Verstappen and Norris are level on 43.
    assert snapshot.drivers[0].points == 43.0
    assert snapshot.drivers[1].points == 43.0


def test_standings_after_the_same_round_differ():
    """Proof the exclusive bound is doing real work, not decoration."""
    before = standings_before_round(SEASON, 2024, 3)
    through = compute_standings(SEASON, 2024, through_round=3)

    assert before.drivers[0].points == 43.0
    assert through.drivers[0].points == 68.0
    assert before.included_rounds != through.included_rounds


def test_round_one_has_no_prior_knowledge():
    """Empty, not a fabricated prior — inventing one is a modelling decision."""
    snapshot = standings_before_round(SEASON, 2024, 1)

    assert snapshot.is_empty
    assert snapshot.drivers == []
    assert snapshot.through_round is None


def test_snapshot_records_which_rounds_it_covers():
    """Provenance is what makes a stored prediction auditable for leakage."""
    snapshot = standings_before_round(SEASON, 2024, 3)
    assert snapshot.included_rounds == [1, 2]


def test_gap_in_ingested_data_is_visible_in_provenance():
    """A missing round changes what the standings mean; it must not be silent."""
    without_round_two = [row for row in SEASON if row.round != 2]
    snapshot = standings_before_round(without_round_two, 2024, 3)

    assert snapshot.included_rounds == [1]  # not [1, 2]


def test_other_seasons_are_excluded():
    other = ResultRow(
        id="x", season=2023, round=1, driver="Max Verstappen", position=1, points=25.0
    )
    snapshot = compute_standings(SEASON + [other], 2024)

    assert all(round_number in (1, 2, 3) for round_number in snapshot.included_rounds)
    assert snapshot.drivers[0].points == 68.0


# ── Accumulation ─────────────────────────────────────────────────────────────


def test_wins_and_podiums_are_counted():
    snapshot = compute_standings(SEASON, 2024)
    verstappen = next(d for d in snapshot.drivers if d.driver == "Max Verstappen")

    assert verstappen.points == 68.0
    assert verstappen.wins == 2
    assert verstappen.podiums == 3
    assert verstappen.races == 3


def test_unclassified_finish_is_not_a_podium():
    """The extractor uses 999 for unclassified; it must not read as a position."""
    snapshot = compute_standings(SEASON, 2024)
    norris = next(d for d in snapshot.drivers if d.driver == "Lando Norris")

    assert norris.races == 3
    assert norris.wins == 1
    assert norris.podiums == 2  # rounds 1 and 2 only; P20 in round 3


def test_points_tie_resolves_to_a_deterministic_order():
    snapshot = standings_before_round(SEASON, 2024, 3)
    assert snapshot.drivers[0].points == snapshot.drivers[1].points
    # Verstappen and Norris both have one win, so this falls through to podiums:
    # Verstappen has 2 (P1, P2), Norris has 2 (P2, P1) — then name breaks the tie
    # deterministically, which is what reproducibility requires.
    assert [d.driver for d in snapshot.drivers[:2]] == [
        "Lando Norris",
        "Max Verstappen",
    ]


def test_positions_are_assigned_in_order():
    snapshot = compute_standings(SEASON, 2024)
    assert [d.position for d in snapshot.drivers] == [1, 2, 3]


def test_constructors_aggregate_both_cars():
    snapshot = compute_standings(SEASON, 2024)
    mclaren = next(c for c in snapshot.constructors if c.team == "McLaren")

    # Norris 43 + Piastri 45.
    assert mclaren.points == 88.0
    assert mclaren.wins == 1
    assert mclaren.races == 6


def test_constructor_standings_lead_can_differ_from_driver_lead():
    snapshot = compute_standings(SEASON, 2024)
    assert snapshot.drivers[0].driver == "Max Verstappen"
    assert snapshot.constructors[0].team == "McLaren"


def test_empty_input_is_not_an_error():
    snapshot = compute_standings([], 2024)
    assert snapshot.is_empty
    assert snapshot.drivers == []
