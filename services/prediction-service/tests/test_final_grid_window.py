"""The confirmed-grid window.

Added because the post-quali window structurally could not be what its name
claimed. The FIA publishes a provisional grid shortly after qualifying and the
final one at exactly T-1h — measured at +1.0h on seven of seven events across
two seasons and six timezones. A window opening at T-18h therefore fires
seventeen hours before the confirmed grid can exist.

That would not matter if the two agreed. Across nine measured events they
differed in six, and five of those changed the pit-lane set — a driver moving
from a grid slot to effectively last, the largest single move available.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.schemas import GRID_WINDOWS, MARKETS_BY_WINDOW, LockWindow
from app.services.scheduler import should_lock, window_opens_at

RACE = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)
QUALI = RACE - timedelta(hours=19)
FINAL_LEAD = timedelta(minutes=45)


# ── Shape ────────────────────────────────────────────────────────────────────


def test_the_final_grid_window_is_grid_conditioned():
    assert LockWindow.FINAL_GRID in GRID_WINDOWS


def test_it_publishes_the_same_markets_as_post_quali():
    """It differs in how sure the grid is, not in what it claims."""
    assert MARKETS_BY_WINDOW[LockWindow.FINAL_GRID] == MARKETS_BY_WINDOW[LockWindow.POST_QUALI]


def test_it_is_a_distinct_window_from_post_quali():
    """They are stored and scored separately — that separation is the point.
    The gap between them measures what the final revisions are worth."""
    assert LockWindow.FINAL_GRID != LockWindow.POST_QUALI


# ── Timing ───────────────────────────────────────────────────────────────────


def test_the_window_opens_forty_five_minutes_before_the_race():
    assert window_opens_at(RACE, FINAL_LEAD) == RACE - timedelta(minutes=45)


def test_it_opens_after_the_fia_publishes_not_before():
    """The document lands at exactly T-1h. Opening at T-45m leaves a
    fifteen-minute margin; opening at T-60m would race the publication."""
    opens = window_opens_at(RACE, FINAL_LEAD)
    fia_publishes = RACE - timedelta(hours=1)
    assert opens > fia_publishes


def test_it_does_not_fire_before_its_window():
    lock, reason = should_lock(
        LockWindow.FINAL_GRID, RACE - timedelta(hours=2), RACE, QUALI, FINAL_LEAD
    )
    assert not lock
    assert "window opens" in reason


def test_it_fires_once_open():
    lock, _ = should_lock(
        LockWindow.FINAL_GRID, RACE - timedelta(minutes=30), RACE, QUALI, FINAL_LEAD
    )
    assert lock


def test_it_never_fires_after_the_race_starts():
    lock, reason = should_lock(
        LockWindow.FINAL_GRID, RACE + timedelta(seconds=1), RACE, QUALI, FINAL_LEAD
    )
    assert not lock
    assert "already started" in reason


def test_qualifying_having_run_does_not_block_it():
    """Unlike pre-quali, this window *requires* qualifying to have happened."""
    lock, _ = should_lock(
        LockWindow.FINAL_GRID, RACE - timedelta(minutes=30), RACE, QUALI, FINAL_LEAD
    )
    assert lock


# ── The defining refusal ─────────────────────────────────────────────────────


async def test_it_refuses_to_publish_on_an_unconfirmed_grid():
    """Its whole premise. Publishing on the qualifying classification here
    would make it indistinguishable from the post-quali forecast, erasing the
    comparison the window was added to make."""
    from app.services.predictor import ConfirmedGridRequired

    from tests.test_api import FakeClient, FakeStore
    from app.services.predictor import Predictor
    from app import dependencies
    from app.services.ingestion_client import GridSlot

    client = FakeClient()

    async def unconfirmed_grid(season, round_number):
        return [
            GridSlot(season=season, round=round_number, driver=d, team="T",
                     position=i + 1, grid_position=i + 1, grid_source="qualifying")
            for i, d in enumerate(
                ["Max Verstappen", "Lando Norris", "Charles Leclerc", "George Russell"]
            )
        ]

    client.grid = unconfirmed_grid
    predictor = Predictor(client=client, store=FakeStore(), model=dependencies.get_model())

    with pytest.raises(ConfirmedGridRequired) as exc:
        await predictor.build(season=2026, round_number=5, window=LockWindow.FINAL_GRID)
    assert "not confirmed" in str(exc.value)


async def test_post_quali_still_accepts_a_provisional_grid():
    """The two windows are supposed to differ here. Post-quali runs on whatever
    grid exists and flags it; only the final-grid window insists."""
    from tests.test_api import FakeClient, FakeStore
    from app.services.predictor import Predictor
    from app import dependencies
    from app.services.ingestion_client import GridSlot

    client = FakeClient()

    async def unconfirmed_grid(season, round_number):
        return [
            GridSlot(season=season, round=round_number, driver=d, team="T",
                     position=i + 1, grid_position=i + 1, grid_source="qualifying")
            for i, d in enumerate(
                ["Max Verstappen", "Lando Norris", "Charles Leclerc", "George Russell"]
            )
        ]

    client.grid = unconfirmed_grid
    predictor = Predictor(client=client, store=FakeStore(), model=dependencies.get_model())

    prediction, _ = await predictor.build(
        season=2026, round_number=5, window=LockWindow.POST_QUALI
    )
    assert prediction.data_quality.grid_is_provisional is True


def test_an_unconfirmed_grid_is_a_409_not_a_500():
    """The caller asked for something not available yet, which is an expected
    state between qualifying and T-1h — not a server fault. Returning 500 would
    put a routine wait into the error budget."""
    from fastapi import HTTPException

    from app.routers.predictions import _handle
    from app.services.predictor import ConfirmedGridRequired

    error = _handle(ConfirmedGridRequired("grid for 2026-12 is not confirmed"))
    assert isinstance(error, HTTPException)
    assert error.status_code == 409
