"""Retirement vs. classification — a bug found against real data.

FastF1 gives a retired car a ``Position`` anyway: the order it stopped in. So
Verstappen's lap-4 retirement from the 2024 Australian GP arrives as "P19",
identical in that field to a genuine 19th place. Reading position alone, his
first five races of 2024 average to 4.6 instead of 1.0 — and the model ranked
the driver who won four of those five races fourth-most-likely to win.

``ClassifiedPosition`` is what separates them ("R" when retired), and ``Status``
is what identifies a car that failed to finish even when it was still
classified. These tests pin both, using the real 2024 Australia data shape.
"""

import pytest

from app.models.schemas import LockWindow
from app.services.features import build_snapshot
from app.services.ingestion_client import RaceResult
from tests.conftest import make_model


def _result(round_number, driver, position, classified, status, points=0.0, team="Red Bull"):
    return RaceResult(
        season=2024, round=round_number, driver=driver, team=team,
        position=position, classified_position=classified, status=status, points=points,
    )


# ── The distinction ──────────────────────────────────────────────────────────


def test_retirement_is_not_a_classified_finish():
    """The real 2024 Australia shape: VER, P19 on the road, ClassifiedPosition R."""
    row = _result(3, "Max Verstappen", 19, "R", "Retired")

    assert not row.classified
    assert row.retired
    assert not row.finished


def test_genuine_last_place_is_a_classified_finish():
    row = _result(3, "Zhou Guanyu", 19, "19", "Lapped")

    assert row.classified
    assert not row.retired
    assert row.finished


def test_a_car_can_be_both_classified_and_retired():
    """Russell, Australia 2024: crashed on the final lap, still classified P17.

    His pace result is genuinely P17; his reliability record must still show a
    retirement. Collapsing these into one flag loses one or the other.
    """
    row = _result(3, "George Russell", 17, "17", "Retired")

    assert row.classified   # P17 is his real result
    assert row.retired      # the car did not finish
    assert row.finished     # usable as a pace signal


def test_missing_classification_falls_back_to_position():
    """Data ingested before this field existed must still behave sensibly."""
    assert _result(1, "X", 5, "", "").classified
    assert not _result(1, "X", 999, "", "").classified
    assert _result(1, "X", 999, "", "").retired


# ── Effect on features ───────────────────────────────────────────────────────


#: Verstappen's actual first five races of 2024: win, win, retirement, win, win.
VERSTAPPEN = [
    _result(1, "Max Verstappen", 1, "1", "Finished", 26.0),
    _result(2, "Max Verstappen", 1, "1", "Finished", 26.0),
    _result(3, "Max Verstappen", 19, "R", "Retired", 0.0),
    _result(4, "Max Verstappen", 1, "1", "Finished", 26.0),
    _result(5, "Max Verstappen", 1, "1", "Finished", 25.0),
]
PEREZ = [
    _result(r, "Sergio Perez", p, str(p), "Finished", 18.0)
    for r, p in [(1, 2), (2, 2), (3, 5), (4, 2), (5, 3)]
]


def test_retirement_does_not_wreck_a_pace_average():
    """The bug: this used to be 4.6, ranking a dominant driver mid-field."""
    snapshot = build_snapshot(VERSTAPPEN + PEREZ, 2024, 6, LockWindow.PRE_QUALI)
    verstappen = next(d for d in snapshot.drivers if d.driver == "Max Verstappen")

    assert verstappen.avg_finish_recent == pytest.approx(1.0)
    assert verstappen.avg_finish_season == pytest.approx(1.0)


def test_retirement_is_counted_as_unreliability():
    """The other half: dnf_rate was 0.00 for the entire field before this fix."""
    snapshot = build_snapshot(VERSTAPPEN + PEREZ, 2024, 6, LockWindow.PRE_QUALI)
    verstappen = next(d for d in snapshot.drivers if d.driver == "Max Verstappen")
    perez = next(d for d in snapshot.drivers if d.driver == "Sergio Perez")

    assert verstappen.dnf_rate == pytest.approx(0.2)  # one in five
    assert perez.dnf_rate == 0.0


#: The rest of a realistic grid. The model's noise scale is fitted on 20-car
#: fields, so a two-driver test is out of distribution: with only two entrants a
#: 20% retirement rate alone is enough to cancel any pace advantage, and the
#: test would be measuring that artefact rather than the bug it exists to guard.
FIELD = [
    _result(r, "Driver {}".format(n), n + 2, str(n + 2), "Finished", 0.0,
            team="Team {}".format(n))
    for n in range(1, 19)
    for r in range(1, 6)
]


def test_the_dominant_driver_is_favoured():
    """The end-to-end assertion the real data falsified.

    Four wins in five races must not produce a lower win probability than a
    team-mate whose best result is second.
    """
    snapshot = build_snapshot(
        VERSTAPPEN + PEREZ + FIELD, 2024, 6, LockWindow.PRE_QUALI
    )
    probabilities = {
        row.driver: row.p_win
        for row in make_model(runs=5000).predict(snapshot.drivers, seed=1)
    }

    assert probabilities["Max Verstappen"] > probabilities["Sergio Perez"]
    # And clear of the field, not merely ahead of one team-mate.
    assert probabilities["Max Verstappen"] == max(probabilities.values())
