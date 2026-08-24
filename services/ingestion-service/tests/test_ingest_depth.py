"""Ingest depth — keeping "complete" honest across two different questions.

Model training needs classifications only (~2s/session). Analytics needs laps,
stints and telemetry (~30s/session). A decade of the latter is hours, so both
paths have to exist — and the moment they do, "is the dataset complete?" stops
having a single answer.

The failure this guards against is the one that already bit us: a training
corpus was built by a side script that bypassed this pipeline, silently ended up
with one qualifying session for a whole season, and nothing noticed until the
fitted weights looked wrong. Routing training through the same completeness
machinery is the fix; depth is what makes that possible without lying.
"""

import pytest

from app.models.schemas import (
    ExpectedSession,
    IngestDepth,
    LapRow,
    ResultRow,
    SessionIngestState,
    SessionPayload,
    SessionState,
)
from app.services.completeness import run_integrity_checks, summarise

MODERN = ExpectedSession(season=2024, round=5, race_name="Test Grand Prix")


def _results(count=20):
    return [
        ResultRow(id="r%d" % i, season=2024, round=5, driver="D%d" % i, position=i)
        for i in range(1, count + 1)
    ]


# ── Integrity is judged against the depth requested ──────────────────────────


def test_results_depth_is_complete_on_classification_alone():
    """A shallow ingest fetched no laps; failing it for absent laps would fill
    the gap list with noise nobody asked for."""
    report = run_integrity_checks(
        SessionPayload(session=MODERN, results=_results()), IngestDepth.RESULTS
    )

    assert report.passed
    assert {c.name for c in report.checks} == {"results_present", "field_size_plausible"}


def test_full_depth_still_demands_lap_data():
    """The shallow path must not weaken the real guarantee."""
    report = run_integrity_checks(
        SessionPayload(session=MODERN, results=_results()), IngestDepth.FULL
    )

    assert not report.passed
    assert any(c.name == "laps_present" and not c.passed for c in report.checks)


def test_results_depth_still_catches_truncation():
    """Shallower does not mean unchecked — a two-car field is still wrong."""
    report = run_integrity_checks(
        SessionPayload(session=MODERN, results=_results(2)), IngestDepth.RESULTS
    )

    assert not report.passed


# ── A shallow success must not satisfy a deep question ───────────────────────


def _state(round_number, state, depth):
    return SessionIngestState(
        season=2024, round=round_number, state=state, depth=depth
    )


def test_results_complete_does_not_count_as_full_complete():
    """The core rule.

    Otherwise a cheap training backfill would mark the whole archive COMPLETE
    and the analytics layer would believe it had lap data it never fetched.
    """
    states = [_state(1, SessionState.COMPLETE, IngestDepth.RESULTS)]

    shallow = summarise(2024, 2024, ["2024-1"], states, depth=IngestDepth.RESULTS)
    deep = summarise(2024, 2024, ["2024-1"], states, depth=IngestDepth.FULL)

    assert shallow.is_complete
    assert not deep.is_complete
    assert deep.open_gaps == ["2024-1"]


def test_full_complete_satisfies_both_questions():
    """A deep ingest is a superset, so it answers the shallow question too."""
    states = [_state(1, SessionState.COMPLETE, IngestDepth.FULL)]

    assert summarise(2024, 2024, ["2024-1"], states, IngestDepth.RESULTS).is_complete
    assert summarise(2024, 2024, ["2024-1"], states, IngestDepth.FULL).is_complete


def test_the_summary_records_which_question_it_answered():
    """A completeness number without its depth is not interpretable."""
    summary = summarise(2024, 2024, ["2024-1"], [], depth=IngestDepth.RESULTS)
    assert summary.depth is IngestDepth.RESULTS


@pytest.mark.parametrize("depth", list(IngestDepth))
def test_a_never_attempted_session_is_a_gap_at_every_depth(depth):
    summary = summarise(2024, 2024, ["2024-1"], [], depth=depth)
    assert summary.open_gaps == ["2024-1"]
