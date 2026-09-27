"""The race that just happened, on the front page.

The page spends most of the week between races. Without this it showed a
circuit and "no forecast locked yet" for five days out of seven, while the
thing the product actually claims — that it publishes first and is marked
afterwards — had just been demonstrated and appeared nowhere.

The failure worth pinning is a panel that reports a race before it has been
scored, or reports the wrong one. Both would put a result on the page ahead of
the record, which is the one order this product cannot get wrong.
"""

import pytest

from app.config import Settings
from app.routers import dashboard


class _Client:
    """Stands in for one downstream service."""

    def __init__(self, routes):
        self._routes = routes

    async def get(self, path, params=None):
        for prefix, payload in self._routes.items():
            if path.startswith(prefix):
                return payload
        return None


def _wire(monkeypatch, scores, results, predictions):
    monkeypatch.setattr(dashboard, "_clients", lambda settings: {
        "scoring": _Client({"/scores": scores}),
        "ingestion": _Client({"/data/results": results}),
        "prediction": _Client({"/predictions": predictions}),
    })


def _score(season, rnd, window, skill=0.4):
    return {
        "season": season, "round": rnd, "window": window,
        "markets": [{"market": "win", "brier": 0.02, "baseline_brier": 0.04,
                     "skill_vs_baseline": skill, "drivers_scored": 22}],
    }


def _result(position, driver, team="Team"):
    return {"position": position, "driver": driver, "team": team,
            "race_name": "Azerbaijan Grand Prix"}


def _prediction(window, ranked):
    return {
        "window": window,
        "driver_probabilities": [
            {"driver": d, "team": "Team", "p_win": p} for d, p in ranked
        ],
    }


RESULTS = [_result(1, "Russell"), _result(2, "Verstappen"), _result(3, "Hadjar")]


async def test_nothing_scored_yet_shows_no_panel(monkeypatch):
    """Null, not an empty shell. A panel with no race in it is worse than no
    panel — and early in a season there genuinely is nothing to show."""
    _wire(monkeypatch, scores=[], results=RESULTS, predictions=[])

    assert await dashboard.last_race(Settings()) is None


async def test_it_reports_the_most_recent_scored_race(monkeypatch):
    _wire(
        monkeypatch,
        scores=[_score(2026, 13, "final_grid"), _score(2026, 15, "final_grid")],
        results=RESULTS,
        predictions=[_prediction("final_grid", [("Russell", 0.33)])],
    )

    view = await dashboard.last_race(Settings())

    assert view.round == 15
    assert view.winner == "Russell"


async def test_it_takes_the_best_informed_window_that_was_scored(monkeypatch):
    """A race scores up to three forecasts. The one worth showing is the one
    that knew the most, by the same preference the rest of the product uses."""
    _wire(
        monkeypatch,
        scores=[
            _score(2026, 15, "pre_quali"),
            _score(2026, 15, "final_grid"),
            _score(2026, 15, "post_quali"),
        ],
        results=RESULTS,
        predictions=[_prediction("final_grid", [("Russell", 0.33)])],
    )

    view = await dashboard.last_race(Settings())

    assert view.window == "final_grid"


async def test_calling_the_winner_is_not_confused_with_the_winner(monkeypatch):
    """Two different facts: who won, and who we said would. The panel's whole
    point is the comparison, so collapsing them would make it say nothing."""
    _wire(
        monkeypatch,
        scores=[_score(2026, 15, "final_grid")],
        results=RESULTS,
        predictions=[_prediction("final_grid", [("Verstappen", 0.40), ("Russell", 0.25)])],
    )

    view = await dashboard.last_race(Settings())

    assert view.winner == "Russell"
    assert view.called_winner == "Verstappen"
    assert view.called_winner_probability == 0.40
    # What we said about the driver who actually won, which is the honest
    # number to show when the favourite loses.
    assert view.winner_probability == 0.25


async def test_a_race_with_no_results_is_not_reported(monkeypatch):
    """Scored but unreadable results means we cannot say who won, and a podium
    invented from a forecast is exactly the thing this product must never do."""
    _wire(
        monkeypatch,
        scores=[_score(2026, 15, "final_grid")],
        results=[],
        predictions=[_prediction("final_grid", [("Russell", 0.33)])],
    )

    assert await dashboard.last_race(Settings()) is None


async def test_a_window_with_no_winner_market_reports_no_call(monkeypatch):
    """The pre-quali window publishes no win probability. Rendering that as a
    0% favourite would turn a refusal into a claim."""
    _wire(
        monkeypatch,
        scores=[_score(2026, 15, "pre_quali")],
        results=RESULTS,
        predictions=[{
            "window": "pre_quali",
            "driver_probabilities": [
                {"driver": "Russell", "team": "Mercedes", "p_win": None},
            ],
        }],
    )

    view = await dashboard.last_race(Settings())

    assert view.called_winner is None
    assert view.called_winner_probability is None
