"""Telling a rate limit apart from an empty session.

Both arrive as an empty frame, and collapsing them cost 47 of 173 sessions on
the 2018-2025 lap backfill. FastF1 allows 500 API calls an hour; past that it
returns 429s. Each one was caught, logged at debug level, and turned into an
empty DataFrame — so the session was recorded as merely lacking lap data, which
is not a fetch failure, so nothing retried it and the log said nothing.

The completeness guarantee did its job: all 50 surfaced as open gaps rather than
passing as whole. What failed was everything downstream of knowing *why*.
"""

import pandas as pd
import pytest

from app.services.fastf1_source import (
    RateLimited,
    SessionFetchError,
    _is_rate_limit,
    _is_transient,
    _session_frame,
)


class _Raises:
    """A session whose attribute raises on access, as FastF1's do."""

    def __init__(self, exc):
        self._exc = exc

    @property
    def laps(self):
        raise self._exc


class _Empty:
    laps = pd.DataFrame()


class RateLimitExceededError(Exception):
    """Named to match FastF1's, which lives in a private module."""


# ── Classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitExceededError("any API: 500 calls/h"),
        Exception("HTTP 429 Too Many Requests"),
        Exception("RateLimit exceeded"),
    ],
)
def test_rate_limits_are_recognised(exc):
    assert _is_rate_limit(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        Exception("Connection reset by peer"),
        Exception("Read timed out"),
        Exception("502 Bad Gateway"),
    ],
)
def test_transient_failures_are_recognised(exc):
    assert _is_transient(exc) is True


def test_a_genuine_absence_is_neither():
    exc = Exception("NoLapDataError: this session has no lap data")
    assert _is_rate_limit(exc) is False
    assert _is_transient(exc) is False


# ── What _session_frame does with each ───────────────────────────────────────


def test_a_rate_limit_raises_rather_than_returning_nothing():
    """The bug in one assertion. Returning an empty frame here is what recorded
    47 sessions as lacking lap data when upstream had simply cut us off."""
    with pytest.raises(RateLimited):
        _session_frame(_Raises(RateLimitExceededError("any API: 500 calls/h")), "laps")


def test_a_transient_failure_is_retryable_not_empty():
    with pytest.raises(SessionFetchError):
        _session_frame(_Raises(Exception("Connection reset by peer")), "laps")


def test_a_session_that_really_has_no_laps_still_yields_an_empty_frame():
    """The behaviour the swallow existed for, and which must survive.

    A raising property once killed a backfill mid-run; the answer is to
    distinguish the causes, not to stop catching.
    """
    frame = _session_frame(_Raises(Exception("NoLapDataError: no lap data")), "laps")
    assert frame.empty


def test_an_ordinarily_empty_attribute_is_unaffected():
    assert _session_frame(_Empty(), "laps").empty


def test_rate_limited_is_a_fetch_error_so_existing_handlers_still_catch_it():
    """Subclassing keeps every current `except SessionFetchError` correct; the
    separate class only lets the backfill stop early instead of churning."""
    assert issubclass(RateLimited, SessionFetchError)
