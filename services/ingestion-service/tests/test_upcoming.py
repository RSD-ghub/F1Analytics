"""Forward-looking tests: next race and grid freshness.

Time is injected everywhere rather than mocked, so these assert on real boundary
conditions (a race in progress, qualifying finished but not ingested) that a live
system only visits for a few hours a year.
"""

from datetime import datetime, timedelta, timezone

from app.models.schemas import RaceWeekend
from app.services.upcoming import next_race, qualifying_freshness, upcoming_races

NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _weekend(round_number: int, days_from_now: float, quali_offset_h: float = -26.0):
    race_start = NOW + timedelta(days=days_from_now)
    return RaceWeekend(
        season=2026,
        round=round_number,
        race_name="Round {}".format(round_number),
        sessions={
            "Qualifying": race_start + timedelta(hours=quali_offset_h),
            "Race": race_start,
        },
        race_start_utc=race_start,
        qualifying_start_utc=race_start + timedelta(hours=quali_offset_h),
    )


# ── Next race ────────────────────────────────────────────────────────────────


def test_next_race_is_the_soonest_future_one():
    weekends = [_weekend(3, 21), _weekend(1, 7), _weekend(2, 14)]
    assert next_race(weekends, at=NOW).round == 1


def test_past_races_are_skipped():
    weekends = [_weekend(1, -14), _weekend(2, -7), _weekend(3, 7)]
    assert next_race(weekends, at=NOW).round == 3


def test_race_in_progress_counts_as_past():
    """Its forecast is already closed; returning it would invite a late pick."""
    weekends = [_weekend(1, -0.02), _weekend(2, 7)]  # started ~30 min ago
    assert next_race(weekends, at=NOW).round == 2


def test_no_future_races_returns_none():
    assert next_race([_weekend(1, -7)], at=NOW) is None


def test_empty_calendar_returns_none():
    assert next_race([], at=NOW) is None


def test_upcoming_races_are_ordered_and_limited():
    weekends = [_weekend(4, 28), _weekend(1, 7), _weekend(3, 21), _weekend(2, 14)]
    result = upcoming_races(weekends, at=NOW, limit=3)

    assert [w.round for w in result] == [1, 2, 3]


def test_weekend_without_a_race_time_is_not_offered_as_upcoming():
    """A TBC calendar entry has no usable date and must not surface as next."""
    undated = RaceWeekend(season=2026, round=9, race_name="TBC")
    result = upcoming_races([undated, _weekend(1, 7)], at=NOW)

    assert [w.round for w in result] == [1]


# ── Grid freshness ───────────────────────────────────────────────────────────


def test_before_qualifying_neither_run_nor_stale():
    freshness = qualifying_freshness(_weekend(1, 7), driver_count=0, at=NOW)

    assert not freshness.has_run
    assert not freshness.has_data
    assert not freshness.is_stale  # nothing is wrong; it just has not happened


def test_after_qualifying_with_data_is_fresh():
    weekend = _weekend(1, 0.5)  # race in 12h, quali was ~14h ago
    freshness = qualifying_freshness(
        weekend, driver_count=20, ingested_at=NOW, at=NOW
    )

    assert freshness.has_run
    assert freshness.has_data
    assert freshness.driver_count == 20
    assert not freshness.is_stale


def test_qualifying_run_but_data_missing_is_stale():
    """The operational alarm: the grid exists in the world but not in our data.

    A naive 'do we have the grid?' check reports this identically to 'qualifying
    has not happened yet', which is why the two questions are tracked apart.
    """
    weekend = _weekend(1, 0.5)
    freshness = qualifying_freshness(weekend, driver_count=0, at=NOW)

    assert freshness.has_run
    assert not freshness.has_data
    assert freshness.is_stale


def test_naive_timestamps_are_treated_as_utc():
    """Mongo round-trips drop tzinfo; a naive value must not blow up a compare."""
    weekend = RaceWeekend(
        season=2026,
        round=1,
        race_start_utc=datetime(2026, 5, 27, 13, 0),
        qualifying_start_utc=datetime(2026, 5, 26, 13, 0),
    )
    freshness = qualifying_freshness(weekend, driver_count=0, at=NOW)

    assert not freshness.has_run
    assert next_race([weekend], at=NOW).round == 1
