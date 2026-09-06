"""Lock windows that fire on their own.

A forecast is only falsifiable because it was committed before a deadline.
Locking by hand quietly destroys that: whoever runs the command chooses the
moment, and "we locked when the numbers looked right" is indistinguishable from
"we locked on schedule" once it is in the database. The scheduler is what makes
the deadline a property of the system rather than of someone's attention.

Three rules the loop enforces, each of which protects a different thing:

**Each window closes at its own boundary, and they are not the same boundary.**
An early version applied one six-hour grace to both, which was wrong: the two
windows are defined by different things.

Pre-quali is defined by *not knowing the grid*, so qualifying is a genuine
contaminating event — a pre-quali forecast placed afterwards is a post-quali
forecast wearing the wrong label, and the scheduler refuses it outright.

Post-quali is defined by *knowing the grid*, and nothing between T-18h and the
race changes that. Refusing at T-12h under a blanket grace meant publishing
nothing at all for a race we could still forecast honestly — strictly worse
than publishing and recording that it was late. So it stays open until the
race, and lateness is recorded rather than used to abstain.

**Never after the race starts.** Obvious, and the one mistake that would be
indefensible in a public record.

**Idempotent.** Re-running is safe: a second lock hits the unique index and is
skipped, so a restart, an overlapping tick, or a manual run cannot double-write.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.models.schemas import LockWindow
from app.services.ingestion_client import IngestionClient, IngestionUnavailable
from app.services.predictor import GridRequired, Predictor
from app.services.storage import PredictionExists

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
JOB_ID = "lock-windows"

#: Lateness beyond this is flagged on the prediction so the track record can
#: account for it. It is a label, not a refusal — see the module docstring.
LATE_AFTER = timedelta(hours=6)


def _aware(value: Any) -> Optional[datetime]:
    """Mongo round-trips drop tzinfo; every comparison here is in UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def window_opens_at(
    race_start: datetime, hours_before: int
) -> datetime:
    return race_start - timedelta(hours=hours_before)


def should_lock(
    window: LockWindow,
    now: datetime,
    race_start: Optional[datetime],
    qualifying_start: Optional[datetime],
    hours_before: int,
) -> tuple:
    """Decide whether to place this forecast now. Returns ``(lock, reason)``.

    The reason is returned even when locking, so the log says why a window
    fired rather than only that it did.
    """
    if race_start is None:
        return False, "no race start time on the calendar"
    if now >= race_start:
        return False, "race has already started"

    opens = window_opens_at(race_start, hours_before)
    if now < opens:
        return False, "window opens {}".format(opens.isoformat())

    # A pre-quali forecast made after qualifying is not late — it is a
    # different forecast. Refusing keeps the two windows comparable, which is
    # the entire reason for having two.
    if window is LockWindow.PRE_QUALI and qualifying_start and now >= qualifying_start:
        return False, "qualifying has already run; a pre-quali lock now would be mislabelled"

    late_by = now - opens
    if late_by > LATE_AFTER:
        # Placed, not refused. Nothing between the window opening and the race
        # changes what a post-quali forecast knows, so abstaining would cost a
        # race for no gain in honesty — recording the delay achieves the same
        # thing and still publishes.
        return True, "window opened {} ago (late)".format(late_by)

    return True, "window open since {}".format(opens.isoformat())


def lateness(now: datetime, race_start: datetime, hours_before: int) -> timedelta:
    return now - window_opens_at(race_start, hours_before)


class LockScheduler:
    def __init__(
        self,
        predictor: Predictor,
        client: IngestionClient,
        pre_quali_hours: int,
        post_quali_hours: int,
    ) -> None:
        self._predictor = predictor
        self._client = client
        self._pre = pre_quali_hours
        self._post = post_quali_hours

    async def tick(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """One pass over the upcoming calendar. Returns what it did and why."""
        moment = now or datetime.now(timezone.utc)
        try:
            weekends = await self._client.weekends(moment.year)
        except IngestionUnavailable as exc:
            logger.warning("cannot read the calendar: %s", exc)
            return []

        outcomes: List[Dict[str, Any]] = []
        for weekend in weekends:
            race_start = _aware(weekend.race_start_utc)
            quali_start = _aware(weekend.qualifying_start_utc)

            for window, hours in (
                (LockWindow.PRE_QUALI, self._pre),
                (LockWindow.POST_QUALI, self._post),
            ):
                lock, reason = should_lock(
                    window, moment, race_start, quali_start, hours
                )
                if not lock:
                    continue
                outcomes.append(
                    await self._lock(weekend, window, reason)
                )
        return outcomes

    async def _lock(self, weekend, window: LockWindow, reason: str) -> Dict[str, Any]:
        key = "{}-{}".format(weekend.season, weekend.round)
        try:
            prediction = await self._predictor.lock(
                season=weekend.season,
                round_number=weekend.round,
                window=window,
                circuit=weekend.circuit,
                race_start_utc=_aware(weekend.race_start_utc),
                race_name=weekend.race_name,
                window_opened_at=window_opens_at(
                    _aware(weekend.race_start_utc),
                    self._pre if window is LockWindow.PRE_QUALI else self._post,
                ),
            )
        except PredictionExists:
            # The normal path on every tick after the first. Not a failure.
            return {"race": key, "window": window.value, "action": "already locked"}
        except GridRequired as exc:
            # Qualifying has not been ingested yet. The window stays open and
            # the next tick will retry — which is why ticks are frequent
            # relative to the grace period.
            logger.info("post-quali for %s waiting on the grid: %s", key, exc)
            return {"race": key, "window": window.value, "action": "waiting for grid"}
        except Exception as exc:  # noqa: BLE001 — one bad race must not stop the sweep
            logger.exception("failed to lock %s %s", key, window.value)
            return {"race": key, "window": window.value, "action": "failed",
                    "detail": str(exc)}

        logger.info("locked %s %s (%s)", key, window.value, reason)
        return {
            "race": key,
            "window": window.value,
            "action": "locked",
            "reason": reason,
            "prediction_id": prediction.prediction_id,
            "complete": prediction.data_quality.complete,
        }


def start(scheduler_service: LockScheduler, interval_minutes: int) -> Optional[AsyncIOScheduler]:
    """Begin the loop. ``interval_minutes <= 0`` disables it."""
    global _scheduler
    if interval_minutes <= 0:
        logger.info("lock scheduler disabled (interval=%s)", interval_minutes)
        return None

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        scheduler_service.tick,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id=JOB_ID,
        # A tick can outrun the interval when it has several races to place;
        # overlapping runs would race each other into the unique index.
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("lock scheduler running every %s minutes", interval_minutes)
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    _scheduler = None
