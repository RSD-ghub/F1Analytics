"""The sweeps that keep the corpus current without being asked.

Three jobs run on a timer here: healing the season, confirming published
grids, and ingesting finished races. All three are idempotent, and all three
had the same defect — a tick whose moment passed while the host was asleep was
logged and discarded.
"""

from app.services import scheduler as ingest_scheduler


class _Runner:
    async def heal_season(self, *a, **k):
        return 0

    async def refresh_pending_grids(self, *a, **k):
        return {}

    async def ingest_finished_races(self, *a, **k):
        return {}


async def test_every_sweep_survives_a_host_that_went_to_sleep():
    """APScheduler's default grace is one second, so on a machine that
    suspends every tick is "missed" and none of them run. The 2026 Azerbaijan
    result sat uningested through four consecutive sweeps, each of which
    logged activity and did nothing — which looks exactly like a scheduler
    that is working.
    """
    started = ingest_scheduler.start(_Runner(), interval_hours=6, grid_check_minutes=30)
    try:
        assert started is not None
        jobs = started.get_jobs()
        assert len(jobs) == 3, "expected all three sweeps, got {}".format(
            [job.id for job in jobs]
        )
        for job in jobs:
            assert job.misfire_grace_time is None, (
                "{} will be dropped when it runs late".format(job.id)
            )
    finally:
        ingest_scheduler.shutdown()


async def test_the_sweeps_can_be_switched_off():
    assert ingest_scheduler.start(_Runner(), interval_hours=0) is None
