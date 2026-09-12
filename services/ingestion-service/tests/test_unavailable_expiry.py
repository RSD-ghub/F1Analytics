"""An "unavailable" session must stop being unavailable.

Caught on a live race. The 2026 Italian Grand Prix was probed seven hours before
its start, correctly recorded as UNAVAILABLE ("race has not run yet"), and then
never looked at again. Six days later the race had run, the results were
published, and `/ingest/gaps` still reported zero gaps while `backfill
--only-gaps` reported "complete, no gaps".

The consequence is worse than a missing row. Forecasts had been locked for that
race, and a result that never ingests is a forecast that can never be scored —
so the track record, which is the entire product, would have stayed empty
forever while reporting itself complete.
"""

from datetime import datetime, timedelta, timezone

from app.models.schemas import SessionIngestState, SessionState
from app.services.completeness import _is_gap, summarise

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
RACE = datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc)


def _state(state, retry_after=None, season=2026, round_number=13):
    return SessionIngestState(
        season=season, round=round_number, state=state, retry_after=retry_after
    )


# ── The bug ──────────────────────────────────────────────────────────────────


def test_a_race_that_has_since_run_becomes_a_gap_again():
    """The exact Monza case."""
    monza = _state(SessionState.UNAVAILABLE, retry_after=RACE)
    assert _is_gap(monza, now=NOW) is True


def test_it_is_not_a_gap_while_the_race_is_still_in_the_future():
    upcoming = _state(SessionState.UNAVAILABLE, retry_after=NOW + timedelta(days=1))
    assert _is_gap(upcoming, now=NOW) is False


def test_the_boundary_is_the_scheduled_start():
    at_start = _state(SessionState.UNAVAILABLE, retry_after=NOW)
    assert _is_gap(at_start, now=NOW) is True


def test_a_session_that_will_never_exist_stays_excluded():
    """A sprint weekend has no FP3 and never will. Re-fetching those on every
    pass would hammer upstream for data that does not exist."""
    never = _state(SessionState.UNAVAILABLE, retry_after=None)
    assert _is_gap(never, now=NOW) is False


def test_a_naive_timestamp_from_mongo_still_compares():
    naive = _state(SessionState.UNAVAILABLE, retry_after=RACE.replace(tzinfo=None))
    assert _is_gap(naive, now=NOW) is True


# ── The states that were already right ───────────────────────────────────────


def test_the_ordinary_gap_states_are_unchanged():
    for state in (SessionState.PENDING, SessionState.PARTIAL, SessionState.FAILED):
        assert _is_gap(_state(state), now=NOW) is True


def test_a_complete_session_is_not_a_gap():
    assert _is_gap(_state(SessionState.COMPLETE), now=NOW) is False


def test_a_session_never_attempted_is_a_gap():
    assert _is_gap(None, now=NOW) is True


# ── Through the summary the healer actually reads ────────────────────────────


def test_the_summary_reopens_a_race_that_has_run():
    summary = summarise(
        2026, 2026,
        expected_keys=["2026-13", "2026-14"],
        states=[
            _state(SessionState.UNAVAILABLE, retry_after=RACE, round_number=13),
            # Next weekend, genuinely still ahead of us.
            _state(SessionState.UNAVAILABLE,
                   retry_after=NOW + timedelta(days=1), round_number=14),
        ],
    )

    assert "2026-13" in summary.open_gaps
    assert "2026-14" not in summary.open_gaps
