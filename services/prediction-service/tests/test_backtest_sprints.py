"""The sprint backtest measures what the live window would have published."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_sprints import backtest, grid_from_result  # noqa: E402

from app.services.ingestion_client import RaceResult


def _row(season, rnd, driver, position, grid=0):
    return RaceResult(
        season=season, round=rnd, driver=driver, team="T{}".format(position),
        position=position, grid_position=grid, classified_position=str(position),
        circuit="Somewhere",
    )


def test_a_pit_lane_start_lines_up_behind_the_grid():
    rows = [_row(2025, 6, "A", 1, grid=2), _row(2025, 6, "B", 2, grid=0),
            _row(2025, 6, "C", 3, grid=1)]
    assert [s.driver for s in grid_from_result(rows)] == ["C", "A", "B"]
    assert all(s.confirmed for s in grid_from_result(rows))


def test_a_sprint_won_from_pole_every_time_shows_skill(model):
    """Twenty sprints in which the grid is the result. A grid-aware model must
    beat a uniform guess on every market, or the harness is broken."""
    drivers = ["D{:02d}".format(i) for i in range(1, 11)]
    results = [
        _row(2025, rnd, d, i + 1, grid=i + 1)
        for rnd in range(1, 21) for i, d in enumerate(drivers)
    ]
    sprints = [
        _row(2025, rnd, d, i + 1, grid=i + 1)
        for rnd in range(5, 21) for i, d in enumerate(drivers)
    ]

    measured = backtest(model, results, sprints)

    assert set(measured) == {2025, 0}
    for market in ("win", "podium", "points"):
        skill, count = measured[0][market]
        assert count == 16
        assert skill > 0, market
