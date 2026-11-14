"""Start the live feed on its own before each race.

Without this, someone had to press Start live on the Data health page before
lights out; asleep or away, the race went unrecorded and the Live page stayed
empty. A scheduled check every minute starts the recorder ``lead`` before a
race's calendar start and keeps the window open for ``WINDOW`` after it, so a
service restarted mid-race picks the feed up again.

Each race is started at most once per process: if the owner presses Stop, the
feed stays stopped. A feed already recording that race is left alone. F1TV
must be connected (see ``live_feed``); when it is not, the reason is put on
the feed's status, which the Data health page shows, instead of retrying a
recorder that cannot connect.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from app.services import live_feed

logger = logging.getLogger(__name__)

#: How long after the calendar start a race may still be picked up.
WINDOW = timedelta(hours=3)

_started: Set[Tuple[int, int]] = set()


def _when(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due(weekends: Iterable[Dict[str, Any]], now: datetime,
        lead: timedelta) -> Optional[Dict[str, Any]]:
    """The race whose recording window is open now, if any."""
    for weekend in weekends:
        start = _when(weekend.get("race_start_utc"))
        if start is not None and start - lead <= now < start + WINDOW:
            return weekend
    return None


async def tick(store, feed: live_feed.LiveFeed, lead_minutes: int,
               now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Start the feed if a race is due and it has not been started yet."""
    if lead_minutes <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    weekends = await store.list_weekends(now.year, now.year)
    weekend = due(weekends, now, timedelta(minutes=lead_minutes))
    if weekend is None:
        return None
    race = (int(weekend["season"]), int(weekend["round"]))
    if race in _started:
        return None
    timing = feed.timing
    if (feed.running and feed.mode == "live" and timing is not None
            and (timing.season, timing.round) == race):
        _started.add(race)
        return None
    if not live_feed.f1tv_connected():
        message = ("auto-start for {} skipped: connect F1TV on the Data health page"
                   .format(weekend.get("race_name") or "{}-{}".format(*race)))
        if feed.error != message:
            logger.warning("live feed: %s", message)
        feed.error = message
        return None
    try:
        registry = await store.recall_drivers(race[0])
    except Exception:
        logger.warning("driver registry unavailable for %s", race[0], exc_info=True)
        registry = {}
    names = {number: entry[0] for number, entry in registry.items()}
    _started.add(race)
    logger.info("live feed: auto-starting %s-%s (%s)", race[0], race[1],
                weekend.get("race_name", ""))
    try:
        return await feed.start(race[0], race[1], names)
    except (live_feed.LiveFeedError, OSError) as exc:
        logger.warning("live feed auto-start failed: %s", exc)
        feed.error = "auto-start failed: {}".format(exc)
        return None
