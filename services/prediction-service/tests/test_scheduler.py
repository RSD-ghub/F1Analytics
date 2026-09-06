"""Lock-window timing.

A forecast is falsifiable only because it was committed before a deadline.
Locking by hand destroys that quietly — whoever runs the command picks the
moment, and once it is in the database "we locked on schedule" and "we locked
when the numbers looked good" are indistinguishable.

So the timing rules are the product guarantee, and these tests are where it
actually lives. Every case below is one where locking would produce a
prediction that is technically well-formed and quietly dishonest.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.schemas import LockWindow
from app.services.scheduler import LATE_AFTER, should_lock, window_opens_at

RACE = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)
QUALI = RACE - timedelta(hours=24)
PRE_HOURS, POST_HOURS = 72, 18


def check(window, now, race=RACE, quali=QUALI, hours=None):
    hours = hours if hours is not None else (
        PRE_HOURS if window is LockWindow.PRE_QUALI else POST_HOURS
    )
    return should_lock(window, now, race, quali, timedelta(hours=hours))


# ── The window opens ─────────────────────────────────────────────────────────


def test_nothing_locks_before_the_window_opens():
    lock, reason = check(LockWindow.PRE_QUALI, RACE - timedelta(hours=96))
    assert not lock
    assert "window opens" in reason


def test_pre_quali_locks_once_its_window_opens():
    lock, _ = check(LockWindow.PRE_QUALI, RACE - timedelta(hours=71))
    assert lock


def test_post_quali_locks_at_its_own_later_window():
    """The two windows are different deadlines, not one deadline twice."""
    at_17h = RACE - timedelta(hours=17)
    assert check(LockWindow.POST_QUALI, at_17h)[0]
    # ...and pre-quali is long past by then.
    assert not check(LockWindow.PRE_QUALI, at_17h)[0]


def test_window_opening_time_is_relative_to_the_race():
    assert window_opens_at(RACE, timedelta(hours=72)) == RACE - timedelta(hours=72)


# ── The rules that stop a dishonest lock ─────────────────────────────────────


def test_pre_quali_will_not_lock_after_qualifying_has_run():
    """The rule that keeps the two windows comparable.

    A pre-quali forecast placed after qualifying is not a late pre-quali
    forecast — it is a post-quali forecast with the wrong label, made with
    information the window is defined by not having. Allowing it would corrupt
    the very comparison the pair exists to support.
    """
    lock, reason = check(LockWindow.PRE_QUALI, QUALI + timedelta(minutes=30))
    assert not lock
    assert "mislabelled" in reason


def test_nothing_locks_once_the_race_has_started():
    """The one mistake that would be indefensible in a public record."""
    for window in LockWindow:
        lock, reason = check(window, RACE + timedelta(minutes=1))
        assert not lock
        assert "already started" in reason


def test_a_late_post_quali_window_still_locks_and_is_flagged():
    """Publishing late beats not publishing.

    Nothing between the post-quali window opening and the race changes what
    that forecast knows — the grid is set either way. An earlier version
    applied a six-hour grace and refused, which meant no forecast at all for a
    race we could still call honestly. Recording the delay achieves the same
    transparency and still publishes.
    """
    lock, reason = check(
        LockWindow.POST_QUALI,
        RACE - timedelta(hours=POST_HOURS) + LATE_AFTER + timedelta(hours=1),
    )
    assert lock
    assert "late" in reason


def test_a_briefly_missed_window_still_locks():
    """A service restart should not silently skip a race."""
    lock, _ = check(
        LockWindow.POST_QUALI, RACE - timedelta(hours=POST_HOURS) + timedelta(hours=1)
    )
    assert lock


def test_a_race_with_no_start_time_is_skipped():
    """A TBC calendar entry has no deadline to be early or late for."""
    lock, reason = should_lock(LockWindow.PRE_QUALI, RACE, None, None, timedelta(hours=PRE_HOURS))
    assert not lock
    assert "no race start" in reason


def test_pre_quali_locks_when_the_calendar_has_no_qualifying_time():
    """Absent a qualifying time we cannot prove qualifying has run, and
    refusing every such race would silently drop it from the record."""
    lock, _ = should_lock(
        LockWindow.PRE_QUALI, RACE - timedelta(hours=71), RACE, None,
        timedelta(hours=PRE_HOURS)
    )
    assert lock


# ── Idempotence and sweep behaviour ──────────────────────────────────────────


class FakeWeekend:
    def __init__(self, season=2026, round_number=13, race_start=RACE, quali=QUALI):
        self.season, self.round = season, round_number
        self.race_name, self.circuit = "Test GP", "Monza"
        self.race_start_utc, self.qualifying_start_utc = race_start, quali


class FakeClient:
    def __init__(self, weekends):
        self._weekends = weekends

    async def weekends(self, season):
        return self._weekends


class FakePredictor:
    def __init__(self, behaviour=None):
        self.calls = []
        self._behaviour = behaviour or {}

    async def lock(self, season, round_number, window, circuit="",
                   race_start_utc=None, race_name="", window_opened_at=None):
        self.calls.append((season, round_number, window))
        self.window_opened_at = window_opened_at
        outcome = self._behaviour.get(window)
        if isinstance(outcome, Exception):
            raise outcome

        class Result:
            prediction_id = "p-{}".format(len(self.calls))

            class data_quality:
                complete = True

        return Result()


def _service(predictor, weekends):
    from app.services.scheduler import LockScheduler

    return LockScheduler(predictor, FakeClient(weekends), PRE_HOURS, POST_HOURS)


async def test_a_tick_locks_the_open_window_only():
    predictor = FakePredictor()
    outcomes = await _service(predictor, [FakeWeekend()]).tick(
        now=RACE - timedelta(hours=71)
    )

    assert [c[2] for c in predictor.calls] == [LockWindow.PRE_QUALI]
    assert outcomes[0]["action"] == "locked"


async def test_running_twice_does_not_lock_twice():
    """The unique index is the real guard; this proves the sweep survives it."""
    from app.services.storage import PredictionExists

    predictor = FakePredictor({LockWindow.PRE_QUALI: PredictionExists("already")})
    outcomes = await _service(predictor, [FakeWeekend()]).tick(
        now=RACE - timedelta(hours=71)
    )

    assert outcomes[0]["action"] == "already locked"


async def test_post_quali_waits_rather_than_failing_when_the_grid_is_absent():
    """Qualifying not yet ingested is a normal state, not an error — the next
    tick retries, which is why ticks are frequent relative to the grace."""
    from app.services.predictor import GridRequired

    predictor = FakePredictor({LockWindow.POST_QUALI: GridRequired("no grid")})
    outcomes = await _service(predictor, [FakeWeekend()]).tick(
        now=RACE - timedelta(hours=17)
    )

    assert outcomes[0]["action"] == "waiting for grid"


async def test_one_failing_race_does_not_abandon_the_sweep():
    predictor = FakePredictor({LockWindow.PRE_QUALI: RuntimeError("boom")})
    weekends = [FakeWeekend(round_number=13), FakeWeekend(round_number=14)]
    outcomes = await _service(predictor, weekends).tick(now=RACE - timedelta(hours=71))

    assert len(outcomes) == 2
    assert {o["action"] for o in outcomes} == {"failed"}


async def test_the_window_opening_time_is_passed_to_the_lock():
    """Stored on the prediction so lateness stays derivable from the record
    alone, rather than having to be reconstructed from a calendar later."""
    predictor = FakePredictor()
    await _service(predictor, [FakeWeekend()]).tick(now=RACE - timedelta(hours=71))

    assert predictor.window_opened_at == RACE - timedelta(hours=PRE_HOURS)


async def test_an_unreachable_calendar_is_survivable():
    from app.services.ingestion_client import IngestionUnavailable
    from app.services.scheduler import LockScheduler

    class Broken:
        async def weekends(self, season):
            raise IngestionUnavailable("ingestion", "down")

    service = LockScheduler(FakePredictor(), Broken(), PRE_HOURS, POST_HOURS)
    assert await service.tick(now=RACE) == []
