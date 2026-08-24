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


def start(runner: IngestRunner, interval_hours: int) -> Optional[AsyncIOScheduler]:
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
    _scheduler.start()
    logger.info("auto-refresh scheduled every %sh", interval_hours)
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    _scheduler = None
