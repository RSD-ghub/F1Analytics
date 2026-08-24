"""Runner tests — the "a failure must not vanish" contract.

The plan's acceptance criterion for this phase is literal: *deliberately break one
session's fetch and confirm it surfaces as an open gap rather than a silent
warning.* ``test_broken_session_becomes_an_open_gap`` is that test. The rest guard
the paths around it — retry, partial data, future races, and healing.

Both collaborators are faked. The point is to exercise the orchestration and state
machine without FastF1 or Mongo, so these run in milliseconds and can assert on
things a live run never reliably reproduces (a session that fails twice then
succeeds, for instance).
"""

from typing import Dict, List, Optional

import pandas as pd
import pytest

from app.models.schemas import (
    ExpectedSession,
    SessionIngestState,
    SessionState,
)
from app.services.fastf1_source import (
    SessionFetchError,
    SessionFrames,
    SessionUnavailableError,
)
from app.services.ingest_runner import IngestRunner


# ── Fakes ────────────────────────────────────────────────────────────────────


def _full_results_frame(count: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Abbreviation": "D{:02d}".format(i),
                "FullName": "Driver {}".format(i),
                "TeamName": "Team {}".format(i),
                "Position": float(i),
                "ClassifiedPosition": str(i),
                "Points": 0.0,
            }
            for i in range(1, count + 1)
        ]
    )


class FakeSource:
    """Stands in for FastF1. Sessions behave however the test dictates."""

    def __init__(self, schedule: Dict[int, List[ExpectedSession]]) -> None:
        self._schedule = schedule
        self.behaviours: Dict[str, object] = {}
        self.load_calls: List[str] = []

    def fetch_schedule(self, season: int) -> List[ExpectedSession]:
        return self._schedule.get(season, [])

    def load_race_frames_results_only(self, expected: ExpectedSession) -> SessionFrames:
        """Shallow ingest resolves to the same fake behaviour as a deep one."""
        return self.load_race(expected)

    def load_race(self, expected: ExpectedSession) -> SessionFrames:
        self.load_calls.append(expected.key)
        behaviour = self.behaviours.get(expected.key)

        if isinstance(behaviour, Exception):
            raise behaviour
        if isinstance(behaviour, list):  # a sequence of outcomes across retries
            outcome = behaviour.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if isinstance(behaviour, SessionFrames):
            return behaviour
        return SessionFrames(session=expected, results=_full_results_frame())


class FakeStore:
    """In-memory stand-in for IngestionStore."""

    def __init__(self) -> None:
        self.expected: Dict[str, ExpectedSession] = {}
        self.states: Dict[str, SessionIngestState] = {}
        self.payloads: Dict[str, Dict[str, int]] = {}

    async def upsert_expected_sessions(self, sessions) -> int:
        for session in sessions:
            self.expected[session.key] = session
        return len(sessions)

    async def list_expected_sessions(self, from_season, to_season):
        return [
            session
            for session in self.expected.values()
            if from_season <= session.season <= to_season
        ]

    async def record_state(self, state: SessionIngestState) -> None:
        self.states[state.key] = state

    async def get_state(self, season, round_number) -> Optional[SessionIngestState]:
        return self.states.get("{}-{}".format(season, round_number))

    async def list_states(self, from_season, to_season):
        return [
            state
            for state in self.states.values()
            if from_season <= state.season <= to_season
        ]

    async def save_payload(self, payload, depth=None) -> Dict[str, int]:
        counts = payload.row_counts()
        self.payloads[payload.session.key] = counts
        return counts


def _session(round_number: int) -> ExpectedSession:
    return ExpectedSession(
        season=2014,  # pre-2018: results-only, so lap checks don't apply
        round=round_number,
        race_name="Race {}".format(round_number),
        race_date="2014-05-0{}".format(round_number),
    )


def _runner(source: FakeSource, store: FakeStore, **kwargs) -> IngestRunner:
    async def no_sleep(_seconds):
        return None

    kwargs.setdefault("sleep", no_sleep)
    return IngestRunner(source=source, store=store, **kwargs)


@pytest.fixture
def setup():
    sessions = [_session(1), _session(2), _session(3)]
    source = FakeSource({2014: sessions})
    store = FakeStore()
    return source, store, sessions


# ── The core guarantee ───────────────────────────────────────────────────────


async def test_broken_session_becomes_an_open_gap(setup):
    """The acceptance test: a broken fetch is a recorded gap, not a log line."""
    source, store, _ = setup
    source.behaviours["2014-2"] = SessionFetchError("connection reset")

    summary = await _runner(source, store).run_backfill(2014, 2014)

    assert summary.open_gaps == ["2014-2"]
    assert not summary.is_complete
    assert summary.complete == 2
    assert summary.failed == 1

    # And the failure is durably queryable, with its reason attached.
    state = await store.get_state(2014, 2)
    assert state.state is SessionState.FAILED
    assert "connection reset" in state.reason

    # The other two races were unaffected — one bad session does not abort the run.
    assert (await store.get_state(2014, 1)).state is SessionState.COMPLETE
    assert (await store.get_state(2014, 3)).state is SessionState.COMPLETE


async def test_a_session_never_attempted_is_still_a_gap(setup):
    """The manifest is written first, so 'we never tried' is visible too."""
    source, store, sessions = setup
    await store.upsert_expected_sessions(sessions)

    summary = await _runner(source, store).status(2014, 2014)

    assert summary.expected == 3
    assert summary.pending == 3
    assert summary.open_gaps == ["2014-1", "2014-2", "2014-3"]


# ── Retry ────────────────────────────────────────────────────────────────────


async def test_transient_failure_is_retried_then_succeeds(setup):
    source, store, sessions = setup
    source.behaviours["2014-1"] = [
        SessionFetchError("timeout"),
        SessionFetchError("timeout"),
        SessionFrames(session=sessions[0], results=_full_results_frame()),
    ]

    state = await _runner(source, store, max_attempts=3).ingest_session(sessions[0])

    assert state.state is SessionState.COMPLETE
    assert state.attempts == 3
    assert source.load_calls.count("2014-1") == 3


async def test_retries_are_bounded(setup):
    source, store, sessions = setup
    source.behaviours["2014-1"] = SessionFetchError("upstream down")

    state = await _runner(source, store, max_attempts=3).ingest_session(sessions[0])

    assert state.state is SessionState.FAILED
    assert source.load_calls.count("2014-1") == 3


async def test_attempts_accumulate_across_runs(setup):
    """Attempt counts persist so a permanently broken session is recognisable."""
    source, store, sessions = setup
    source.behaviours["2014-1"] = SessionFetchError("upstream down")
    runner = _runner(source, store, max_attempts=2)

    await runner.ingest_session(sessions[0])
    state = await runner.ingest_session(sessions[0])

    assert state.attempts == 4


async def test_future_race_is_not_retried_and_is_not_a_gap(setup):
    """Asking three times will not make an unrun race exist."""
    source, store, sessions = setup
    source.behaviours["2014-2"] = SessionUnavailableError("race has not run yet")

    summary = await _runner(source, store).run_backfill(2014, 2014)

    assert source.load_calls.count("2014-2") == 1
    assert (await store.get_state(2014, 2)).state is SessionState.UNAVAILABLE
    assert summary.open_gaps == []
    assert summary.is_complete


# ── Partial data ─────────────────────────────────────────────────────────────


async def test_truncated_data_is_stored_but_stays_an_open_gap(setup):
    """Partial data beats none, but it must not be mistaken for complete."""
    source, store, sessions = setup
    source.behaviours["2014-2"] = SessionFrames(
        session=sessions[1], results=_full_results_frame(count=3)
    )

    summary = await _runner(source, store).run_backfill(2014, 2014)

    state = await store.get_state(2014, 2)
    assert state.state is SessionState.PARTIAL
    assert "field_size_plausible" in state.reason
    assert store.payloads["2014-2"]["results"] == 3  # stored anyway
    assert summary.open_gaps == ["2014-2"]


async def test_unexpected_errors_are_recorded_not_propagated(setup):
    """A crash mid-session must still leave evidence behind."""
    source, store, sessions = setup
    source.behaviours["2014-1"] = ValueError("something nobody predicted")

    state = await _runner(source, store, max_attempts=1).ingest_session(sessions[0])

    assert state.state is SessionState.FAILED
    assert "something nobody predicted" in state.reason


# ── Healing ──────────────────────────────────────────────────────────────────


async def test_gap_healing_reingests_only_what_is_missing(setup):
    source, store, _ = setup
    source.behaviours["2014-2"] = SessionFetchError("connection reset")
    runner = _runner(source, store)

    first = await runner.run_backfill(2014, 2014)
    assert first.open_gaps == ["2014-2"]

    # Upstream recovers.
    source.behaviours.pop("2014-2")
    source.load_calls.clear()
    healed = await runner.run_backfill(2014, 2014, only_gaps=True)

    assert source.load_calls == ["2014-2"]  # the two complete races were skipped
    assert healed.open_gaps == []
    assert healed.is_complete


async def test_row_counts_are_recorded_on_success(setup):
    source, store, sessions = setup

    state = await _runner(source, store).ingest_session(sessions[0])

    assert state.row_counts["results"] == 20
    assert state.completed_at is not None
