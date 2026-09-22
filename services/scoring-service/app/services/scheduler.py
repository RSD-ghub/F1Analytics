"""Scoring that happens without being asked.

This service could compute a track record and never once did so on its own.
Every score in it existed because somebody remembered to POST /reconcile after a
race — which makes the product's central promise conditional on attention. An
unscored forecast is indistinguishable from a hidden one, and the whole reason
the record counts locked-but-unscored predictions is that declining to
reconcile the bad ones would otherwise flatter the average. Leaving the trigger
manual left that door open at the other end.

Safe to run on a timer because reconciliation is idempotent twice over: outcomes
are replaced per race, and scores upsert on ``prediction_id``. Re-running scores
nothing twice and costs one query per round.

Cheap, too. It only looks at rounds that already have a locked prediction, so
outside a race weekend the sweep finds nothing to do and stops.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.clients import UpstreamUnavailable
from app.services.reconciler import Reconciler

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
JOB_ID = "reconcile-current-season"


#: Waits before each re-attempt when the upstream cannot be reached, in seconds.
#:
#: Sized for a cold start, which is the case this exists for. The whole stack
#: coming up at once — ``docker-compose up`` on the VM, or a restart of all four
#: services — has scoring firing its startup sweep while prediction is still
#: binding its port, and the sweep then sat idle for a full interval over a gap
#: that closed in seconds. Observed locally: prediction took about eight seconds
#: to load its weights and listen.
#:
#: Deliberately bounded and short. This is not a substitute for the interval —
#: an upstream that is genuinely down stays down, and the next tick will find it
#: soon enough. It only covers the seconds-long window where the answer is "not
#: yet" rather than "no".
RETRY_DELAYS = (2.0, 5.0, 15.0, 30.0)


async def reconcile_current_season(
    reconciler: Reconciler,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Score every forecast whose race has finished and whose results have landed.

    Retries a few times when the upstream is unreachable, then gives up until
    the next tick. Only ``UpstreamUnavailable`` is retried: it is the one
    failure that routinely resolves on its own within seconds. Anything else is
    a bug or a bad response, and re-running it four times produces the same
    answer four times more slowly.

    Failures are logged rather than raised: APScheduler drops an exception
    silently and the next tick would look identical, so a job that cannot report
    its own failure is a job that stops working invisibly — which is the exact
    shape of the bug this scheduler exists to fix.

    ``sleep`` is injectable so the retry path can be tested without waiting for
    it.
    """
    season = datetime.now(timezone.utc).year
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            scored = await reconciler.reconcile_season(season)
        except UpstreamUnavailable as exc:
            if attempt + 1 >= attempts:
                logger.warning(
                    "cannot reconcile %s right now, gave up after %d attempts: %s",
                    season, attempts, exc,
                )
                return
            logger.debug(
                "upstream not ready for %s (attempt %d/%d), retrying in %ss: %s",
                season, attempt + 1, attempts, RETRY_DELAYS[attempt], exc,
            )
            await sleep(RETRY_DELAYS[attempt])
        except Exception:
            logger.exception("scheduled reconciliation failed for %s", season)
            return
        else:
            if attempt:
                logger.info(
                    "reconciled %s on attempt %d; upstream was still starting",
                    season, attempt + 1,
                )
            break

    if scored:
        logger.info(
            "reconciled %s: %d forecast(s) scored — %s",
            season, len(scored),
            ", ".join(sorted({"R{} {}".format(s.round, s.window) for s in scored})),
        )


def start(reconciler: Reconciler, interval_minutes: int) -> Optional[AsyncIOScheduler]:
    """Begin the sweep. ``interval_minutes <= 0`` disables it."""
    global _scheduler
    if interval_minutes <= 0:
        logger.info("automatic reconciliation disabled (interval=%s)", interval_minutes)
        return None

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        reconcile_current_season,
        trigger=IntervalTrigger(minutes=interval_minutes),
        args=[reconciler],
        id=JOB_ID,
        # One sweep at a time: a slow upstream must not let two runs race each
        # other into the same score document.
        max_instances=1,
        coalesce=True,
        # Fire at startup rather than one interval later. A restart shortly
        # after a race would otherwise leave that race unscored for the whole
        # interval, and the same omission cost a live lock window earlier in
        # this project.
        next_run_time=datetime.now(timezone.utc),
    )
    _scheduler.start()
    logger.info("automatic reconciliation every %s minutes", interval_minutes)
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    _scheduler = None
