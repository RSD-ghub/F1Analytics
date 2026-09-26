"""Reconciliation that runs itself.

Until this existed, every score in the track record was there because somebody
remembered to POST /reconcile after a race. That makes the product's central
claim conditional on attention: the record deliberately counts locked-but-
unscored forecasts so that declining to score the bad ones cannot flatter the
average, and a manual trigger left exactly that door open at the other end.
"""

from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services import scheduler as reconcile_scheduler
from app.services.clients import UpstreamUnavailable


class _Reconciler:
    def __init__(self, scored=None, raises=None, raises_first=0):
        self._scored = scored or []
        self._raises = raises
        #: Fail this many opening calls, then behave. Models an upstream that is
        #: starting rather than one that is absent.
        self._raises_first = raises_first
        self.seasons = []

    async def reconcile_season(self, season):
        self.seasons.append(season)
        if self._raises_first:
            self._raises_first -= 1
            raise UpstreamUnavailable("prediction-service still starting")
        if self._raises:
            raise self._raises
        return self._scored


class _Clock:
    """Records what the retry would have waited, without waiting."""

    def __init__(self):
        self.waits = []

    async def __call__(self, seconds):
        self.waits.append(seconds)


async def test_it_sweeps_the_current_season():
    reconciler = _Reconciler()
    await reconcile_scheduler.reconcile_current_season(reconciler)
    assert reconciler.seasons == [datetime.now(timezone.utc).year]


async def test_an_unreachable_upstream_does_not_kill_the_job():
    """APScheduler drops an exception silently and the next tick looks
    identical, so a job that cannot survive a blip stops working invisibly."""
    reconciler = _Reconciler(raises=UpstreamUnavailable("prediction-service down"))
    # Injected clock, or this test spends the whole retry budget sleeping.
    await reconcile_scheduler.reconcile_current_season(
        reconciler, sleep=_Clock()
    )  # must not raise


async def test_an_unexpected_failure_does_not_kill_the_job_either():
    reconciler = _Reconciler(raises=RuntimeError("something else"))
    await reconcile_scheduler.reconcile_current_season(reconciler)


async def test_it_fires_at_startup_not_one_interval_later():
    """A restart shortly after a race would otherwise leave it unscored for a
    whole interval. The same omission cost a live lock window earlier on."""
    started = reconcile_scheduler.start(_Reconciler(), interval_minutes=30)
    try:
        assert isinstance(started, AsyncIOScheduler)
        job = started.get_job(reconcile_scheduler.JOB_ID)
        delay = (job.next_run_time - datetime.now(timezone.utc)).total_seconds()
        assert delay < 60, "first sweep is {}s away; should be immediate".format(delay)
    finally:
        reconcile_scheduler.shutdown()


async def test_it_can_be_switched_off():
    assert reconcile_scheduler.start(_Reconciler(), interval_minutes=0) is None


async def test_it_waits_for_an_upstream_that_is_still_starting():
    """The cold-start race this retry exists for.

    Bringing the whole stack up at once has scoring firing its startup sweep
    while prediction is still binding its port. Before the retry that cost a
    full interval of doing nothing over a gap that closed in seconds.
    """
    reconciler = _Reconciler(raises_first=2)
    clock = _Clock()
    await reconcile_scheduler.reconcile_current_season(reconciler, sleep=clock)

    assert len(reconciler.seasons) == 3, "gave up before the upstream was up"
    assert clock.waits == list(reconcile_scheduler.RETRY_DELAYS[:2])


async def test_it_gives_up_on_an_upstream_that_is_simply_down():
    """Bounded, not indefinite. An upstream that is genuinely down stays down,
    and the next tick will find it soon enough."""
    reconciler = _Reconciler(raises=UpstreamUnavailable("down"))
    clock = _Clock()
    await reconcile_scheduler.reconcile_current_season(reconciler, sleep=clock)

    attempts = len(reconcile_scheduler.RETRY_DELAYS) + 1
    assert len(reconciler.seasons) == attempts
    assert sum(clock.waits) < 120, "a single sweep should not outlast its interval"


async def test_it_does_not_retry_a_real_failure():
    """Only an unreachable upstream resolves on its own. Anything else is a bug
    or a bad response, and re-running it produces the same answer more slowly."""
    reconciler = _Reconciler(raises=RuntimeError("bad response"))
    clock = _Clock()
    await reconcile_scheduler.reconcile_current_season(reconciler, sleep=clock)

    assert len(reconciler.seasons) == 1
    assert clock.waits == []


async def test_a_tick_missed_while_the_host_slept_still_runs():
    """APScheduler's default grace is one second, so a job whose moment passed
    while the process was frozen is logged as "missed" and never run — which
    is indistinguishable from a healthy scheduler. Four consecutive sweeps
    were lost that way on the day of the 2026 Azerbaijan race."""
    started = reconcile_scheduler.start(_Reconciler(), interval_minutes=30)
    try:
        job = started.get_job(reconcile_scheduler.JOB_ID)
        assert job.misfire_grace_time is None, (
            "a late sweep will be dropped instead of run"
        )
    finally:
        reconcile_scheduler.shutdown()
