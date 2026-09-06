"""Periodic gap healing.

Completeness is not a one-off migration. New races happen, upstream backfills
data that was missing at first fetch, and transient failures leave gaps that would
otherwise sit there forever. So the healing pass runs on a schedule and is
deliberately narrow: current season, gaps only.

Historical backfills stay manual (``POST /ingest/backfill``) — a 2010-onwards run
is hours of work and should be a decision, not a side effect of a restart.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.ingest_runner import IngestRunner

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None

JOB_ID = "heal-current-season"
GRID_JOB_ID = "confirm-starting-grids"


async def heal_current_season(runner: IngestRunner) -> None:
    """Re-ingest anything in the current season that is not yet whole."""
    season = datetime.now(timezone.utc).year
    try:
        summary = await runner.run_backfill(season, season, only_gaps=True)
        if summary.open_gaps:
            logger.warning(
                "scheduled heal left %s open gap(s) in %s: %s",
                len(summary.open_gaps),
                season,
                ", ".join(summary.open_gaps),
            )
        else:
            logger.info("scheduled heal: season %s is complete", season)
    except Exception:
        # A scheduled job that raises would be silently dropped by APScheduler
        # and the next tick would look identical, so failures are logged here.
        logger.exception("scheduled heal failed for season %s", season)


async def confirm_starting_grids(runner: IngestRunner) -> None:
    """Pick up official starting grids as the stewards publish them.

    Runs far more often than the healing pass because it is chasing a much
    shorter deadline. Qualifying ends roughly a day before the race; the FIA
    grid document follows within a few hours; the post-quali forecast locks in
    between. Checking once a day would reliably miss that window — and missing
    it means publishing a forecast that models penalised drivers from the wrong
    slot, which is the whole problem this is here to solve.

    One PDF per pending round, and only for rounds that have qualified and not
    yet raced, so the cost stays near zero outside a race weekend.
    """
    season = datetime.now(timezone.utc).year
    try:
        await runner.refresh_pending_grids(season)
    except Exception:
        logger.exception("scheduled grid confirmation failed for %s", season)


def start(runner: IngestRunner, interval_hours: int, grid_check_minutes: int = 30) -> Optional[AsyncIOScheduler]:
    """Start the healing job. ``interval_hours <= 0`` disables it."""
    global _scheduler
    if interval_hours <= 0:
        logger.info("auto-refresh disabled (auto_refresh_hours=%s)", interval_hours)
        return None

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        heal_current_season,
        trigger=IntervalTrigger(hours=interval_hours),
        args=[runner],
        id=JOB_ID,
        # A backfill can outrun the interval; overlapping runs would fight over
        # the same sessions and double the upstream load for no benefit.
        max_instances=1,
        coalesce=True,
    )
    if grid_check_minutes > 0:
        _scheduler.add_job(
            confirm_starting_grids,
            trigger=IntervalTrigger(minutes=grid_check_minutes),
            args=[runner],
            id=GRID_JOB_ID,
            # Same reasoning as the lock scheduler: a restart must not postpone
            # the first look for a newly published grid by a whole interval.
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
            coalesce=True,
        )
        logger.info("grid confirmation scheduled every %smin", grid_check_minutes)

    _scheduler.start()
    logger.info("auto-refresh scheduled every %sh", interval_hours)
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    _scheduler = None
