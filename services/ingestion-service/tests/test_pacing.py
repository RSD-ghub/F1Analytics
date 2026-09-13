"""Pacing a backfill under FastF1's hourly quota.

500 API calls an hour, several per full-depth session. An unpaced 2018-2025 run
spent the quota in minutes, stopped at 123 of 173 sessions, and needed four
passes across four hours to finish. Stopping was the right response once the
limit was hit — seconds of retry cannot clear an hourly quota — but not hitting
it is better.
"""

import time

import pytest

from app.services.ingest_runner import _Pacer


async def test_the_first_session_is_not_delayed():
    """Nothing has been spent yet, so there is nothing to wait for."""
    pacer = _Pacer(sessions_per_hour=3600)  # 1s spacing
    started = time.monotonic()
    await pacer.wait()
    assert time.monotonic() - started < 0.1


async def test_subsequent_sessions_are_spaced():
    pacer = _Pacer(sessions_per_hour=36000)  # 0.1s spacing
    await pacer.wait()
    started = time.monotonic()
    await pacer.wait()
    assert time.monotonic() - started >= 0.05


async def test_time_already_spent_counts_toward_the_interval():
    """A session that took longer than the interval should not then wait again —
    the quota is spent by elapsed time, not by sitting idle."""
    pacer = _Pacer(sessions_per_hour=36000)  # 0.1s spacing
    await pacer.wait()
    time.sleep(0.12)  # the ingest itself took longer than the interval
    started = time.monotonic()
    await pacer.wait()
    assert time.monotonic() - started < 0.05


async def test_zero_disables_pacing():
    """Right for the single-session endpoints and for tests: one session cannot
    exhaust an hourly quota, and a wait there is pure latency."""
    pacer = _Pacer(sessions_per_hour=0)
    await pacer.wait()
    started = time.monotonic()
    await pacer.wait()
    assert time.monotonic() - started < 0.05


def test_the_default_rate_stays_under_the_quota():
    """55 sessions an hour against 500 calls an hour leaves room for roughly
    nine calls per session, comfortably above what a full-depth load spends."""
    from app.config import Settings

    rate = Settings().backfill_sessions_per_hour
    assert 0 < rate <= 60
    assert 500 / rate >= 8
