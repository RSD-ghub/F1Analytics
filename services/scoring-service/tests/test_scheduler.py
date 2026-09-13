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
    def __init__(self, scored=None, raises=None):
        self._scored = scored or []
        self._raises = raises
        self.seasons = []

    async def reconcile_season(self, season):
        self.seasons.append(season)
        if self._raises:
            raise self._raises
        return self._scored


async def test_it_sweeps_the_current_season():
    reconciler = _Reconciler()
    await reconcile_scheduler.reconcile_current_season(reconciler)
    assert reconciler.seasons == [datetime.now(timezone.utc).year]


async def test_an_unreachable_upstream_does_not_kill_the_job():
    """APScheduler drops an exception silently and the next tick looks
    identical, so a job that cannot survive a blip stops working invisibly."""
    reconciler = _Reconciler(raises=UpstreamUnavailable("prediction-service down"))
    await reconcile_scheduler.reconcile_current_season(reconciler)  # must not raise


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
