"""Tell someone when a race weekend went wrong.

The schedulers log their failures and carry on, which is right for them but
means a missed lock window is only discovered by someone reading logs. A missed
lock cannot be redone, since a forecast placed after the fact is not a
forecast, so it needs a person to hear about it the same day.

Two checks, over races from the last week:

**A lock window passed with no forecast.** Once the race has started, every
expected window (by default pre-quali, post-quali and final grid, plus the
sprint on a sprint weekend) should have a locked forecast. A missing one means
a service was down, the host was asleep, or a lock was refused.

**A race was not scored.** A day and a bit after the race, results are in and
every locked forecast should be scored. One that is not is either stuck on
missing results or held for a driver-name mismatch, and the alert says which.

Each problem is sent once (recorded in ``alerts_sent``). A failed delivery is
not recorded, so the next check tries again. If the host itself is asleep,
nothing runs; the check catches up as soon as it wakes.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from app.models.schemas import RACE, SPRINT, SPRINT_WINDOWS
from app.services.alerts import Notifier
from app.services.clients import CalendarWeekend, UpstreamUnavailable

logger = logging.getLogger(__name__)

#: Only races this recent are checked, so a first run does not page about the
#: whole season's history.
LOOKBACK = timedelta(days=7)

#: How long after the start before a missing lock is reported. Locks are
#: placed before the start; the margin only absorbs clock skew and a slow tick.
LOCK_GRACE = timedelta(minutes=30)

#: How long after the start before a missing score is reported. Results have
#: landed most of a day late before (Monza), so this is generous.
SCORE_GRACE = timedelta(hours=30)


class Alert:
    def __init__(self, key: str, title: str, message: str) -> None:
        self.key, self.title, self.message = key, title, message

    def __repr__(self) -> str:
        return "Alert({!r})".format(self.key)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _label(weekend: CalendarWeekend) -> str:
    name = weekend.race_name or "Round {}".format(weekend.round)
    return "{} {} (R{})".format(weekend.season, name, weekend.round)


class Watchdog:
    def __init__(self, ingestion, predictions, store, notifier: Notifier,
                 expected_windows: Sequence[str] = ("pre_quali", "post_quali", "final_grid")) -> None:
        self._ingestion = ingestion
        self._predictions = predictions
        self._store = store
        self._notifier = notifier
        self._expected = [w for w in expected_windows if w]

    async def check(self, now: Optional[datetime] = None) -> List[Alert]:
        """Every problem found right now, sent or not."""
        moment = now or datetime.now(timezone.utc)
        seasons = sorted({moment.year, (moment - LOOKBACK).year})
        alerts: List[Alert] = []
        for season in seasons:
            for weekend in await self._ingestion.calendar(season):
                alerts.extend(await self._check_weekend(weekend, moment))
        return alerts

    async def run(self, now: Optional[datetime] = None) -> List[Alert]:
        """Check, then deliver each new alert once. Returns what was sent."""
        moment = now or datetime.now(timezone.utc)
        try:
            found = await self.check(moment)
        except UpstreamUnavailable as exc:
            logger.warning("watchdog could not read the calendar or forecasts: %s", exc)
            return []
        sent: List[Alert] = []
        for alert in found:
            if await self._store.alert_sent(alert.key):
                continue
            delivered = await self._notifier.send(alert.title, alert.message)
            # Without a webhook there is nowhere to retry to; record it so the
            # log is not repeated every hour.
            if delivered or not self._notifier.configured:
                await self._store.mark_alert_sent(alert.key, alert.message, moment)
            if delivered:
                sent.append(alert)
        return sent

    async def send_test(self) -> bool:
        return await self._notifier.send(
            "F1 Forecast test alert",
            "If you can read this, missed-lock and unscored-race alerts will reach you.",
        )

    async def _check_weekend(self, weekend: CalendarWeekend, now: datetime) -> List[Alert]:
        race_start = _aware(weekend.race_start_utc)
        if race_start is None or race_start < now - LOOKBACK or race_start > now:
            return []

        alerts: List[Alert] = []
        locked = await self._predictions.predictions_for(
            weekend.season, weekend.round, event="all"
        )
        windows = {p.window for p in locked}

        expected = []
        if now >= race_start + LOCK_GRACE:
            expected.extend(self._expected)
        sprint_start = _aware(weekend.sprint_start_utc)
        if weekend.is_sprint_weekend and sprint_start and now >= sprint_start + LOCK_GRACE:
            expected.extend(SPRINT_WINDOWS)
        missing = [w for w in expected if w not in windows]
        if missing:
            alerts.append(Alert(
                key="missed-lock:{}-{}:{}".format(
                    weekend.season, weekend.round, ",".join(sorted(missing))
                ),
                title="Missed forecast lock: {}".format(_label(weekend)),
                message=(
                    "No locked forecast for {} window(s): {}. The race has "
                    "started, so these cannot be locked now. Check that "
                    "prediction-service was running and read its log for "
                    "the lock attempts."
                ).format(len(missing), ", ".join(missing)),
            ))

        if now >= race_start + SCORE_GRACE:
            alerts.extend(await self._unscored(weekend, locked))
        return alerts

    async def _unscored(self, weekend: CalendarWeekend, locked) -> List[Alert]:
        alerts: List[Alert] = []
        for event in (RACE, SPRINT):
            forecasts = [
                p for p in locked
                if p.event == event and not p.is_late_lock
            ]
            if not forecasts:
                continue
            unscored = [
                p for p in forecasts
                if await self._store.get_score(p.prediction_id) is None
            ]
            if not unscored:
                continue
            outcome = await self._store.get_outcome(weekend.season, weekend.round, event)
            held = {h.prediction_id: h for h in (outcome.held if outcome else [])}
            if held:
                names = sorted({
                    "{} / {}".format(m.forecast_name, m.result_name)
                    for h in held.values() for m in h.mismatches
                })
                reason = (
                    "held because the forecast and the results spell a driver "
                    "differently ({}). Fix the names in the database, then run "
                    "POST /reconcile/{}/{} on scoring-service."
                ).format("; ".join(names), weekend.season, weekend.round)
            elif outcome is None:
                reason = (
                    "no results with a classified winner have been ingested "
                    "yet. Check ingestion-service."
                )
            else:
                reason = "results are in but scoring did not run. Check scoring-service's log."
            what = "sprint" if event == SPRINT else "race"
            alerts.append(Alert(
                key="unscored:{}-{}:{}".format(weekend.season, weekend.round, event),
                title="Unscored {}: {}".format(what, _label(weekend)),
                message="{} locked forecast(s) not scored: {}".format(
                    len(unscored), reason
                ),
            ))
        return alerts
