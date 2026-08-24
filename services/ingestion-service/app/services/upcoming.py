"""Forward-looking view: what races are coming, and is the grid known yet?

This is the half of the system the old app never had. Everything in Phase 2 looks
backwards at races that have run; these functions answer "what happens next",
which is what a forecasting product is actually built on.

Deliberately no lock-window arithmetic here. Where the pre-quali and post-quali
deadlines fall is a prediction-service decision (it owns those config values and
must be able to change them without redeploying ingestion). This service answers
only the factual questions — when do sessions start, and has qualifying data
landed — and prediction-service places its deadlines against those facts.
"""

from datetime import datetime, timezone
from typing import List, Optional, Sequence

from app.models.schemas import QualifyingFreshness, RaceWeekend


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    """Mongo round-trips can drop tzinfo; everything here compares in UTC."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def next_race(
    weekends: Sequence[RaceWeekend], at: Optional[datetime] = None
) -> Optional[RaceWeekend]:
    """The next race that has not yet started.

    A race currently in progress counts as past: a forecast for it is already
    closed, and returning it as "next" would invite predictions against a race
    being run.
    """
    moment = at or now_utc()
    future = [
        weekend
        for weekend in weekends
        if (_aware(weekend.race_start_utc) or datetime.max.replace(tzinfo=timezone.utc))
        > moment
    ]
    if not future:
        return None
    return min(future, key=lambda w: _aware(w.race_start_utc))


def upcoming_races(
    weekends: Sequence[RaceWeekend],
    at: Optional[datetime] = None,
    limit: int = 5,
) -> List[RaceWeekend]:
    """The next ``limit`` races, soonest first."""
    moment = at or now_utc()
    future = [
        weekend
        for weekend in weekends
        if _aware(weekend.race_start_utc) and _aware(weekend.race_start_utc) > moment
    ]
    future.sort(key=lambda w: _aware(w.race_start_utc))
    return future[:limit]


def qualifying_freshness(
    weekend: RaceWeekend,
    driver_count: int,
    ingested_at: Optional[datetime] = None,
    at: Optional[datetime] = None,
) -> QualifyingFreshness:
    """Has qualifying run, and has its data actually landed?

    The two questions are tracked separately because the gap between them is the
    thing that matters: qualifying finished an hour ago and we still have no grid
    is a live operational problem, and it is exactly the state a naive "do we
    have data?" check reports as an ordinary no.
    """
    moment = at or now_utc()
    start = _aware(weekend.qualifying_start_utc)

    return QualifyingFreshness(
        season=weekend.season,
        round=weekend.round,
        qualifying_start_utc=start,
        has_run=bool(start and start <= moment),
        has_data=driver_count > 0,
        driver_count=driver_count,
        ingested_at=_aware(ingested_at),
    )
