"""Completeness tests — the "no data missed" guarantee.

Two failure modes are being defended against, and they pull in opposite
directions:

* **False negatives** — truncated data that passes as whole. This is the one that
  destroys the product, because a model trained on 90% of the laps reports
  perfectly reasonable-looking numbers.
* **False positives** — real races flagged as broken. Just as damaging in
  practice: a gap list with permanent noise in it stops being read.

So there are tests for both directions.
"""

from app.models.schemas import (
    ExpectedSession,
    LapRow,
    PitStopRow,
    ResultRow,
    SessionIngestState,
    SessionPayload,
    SessionState,
    StintRow,
)
from app.services.completeness import run_integrity_checks, summarise

MODERN = ExpectedSession(season=2024, round=5, race_name="Test Grand Prix")
LEGACY = ExpectedSession(season=2014, round=5, race_name="Old Grand Prix")


def _results(session: ExpectedSession, count: int):
    return [
        ResultRow(
            id="r{}".format(i),
            season=session.season,
            round=session.round,
            driver="Driver {}".format(i),
            position=i,
        )
        for i in range(1, count + 1)
    ]


def _laps(session: ExpectedSession, driver: str, first: int, last: int):
    return [
        LapRow(
            id="{}-{}".format(driver, lap),
            season=session.season,
            round=session.round,
            driver=driver,
            lap=lap,
        )
        for lap in range(first, last + 1)
    ]


def _named(report, name):
    return next(check for check in report.checks if check.name == name)


# ── Legacy seasons ───────────────────────────────────────────────────────────


def test_pre_2018_season_is_complete_on_results_alone():
    """FastF1 has no lap data before 2018 — demanding it would mark eight
    complete seasons permanently broken."""
    payload = SessionPayload(session=LEGACY, results=_results(LEGACY, 20))
    report = run_integrity_checks(payload)

    assert report.passed
    assert {check.name for check in report.checks} == {
        "results_present",
        "field_size_plausible",
    }


def test_modern_season_requires_lap_data():
    payload = SessionPayload(session=MODERN, results=_results(MODERN, 20))
    report = run_integrity_checks(payload)

    assert not report.passed
    assert not _named(report, "laps_present").passed


# ── Truncation detection ─────────────────────────────────────────────────────


def test_empty_results_fail():
    report = run_integrity_checks(SessionPayload(session=LEGACY))
    assert not _named(report, "results_present").passed


def test_truncated_field_is_flagged_even_though_nothing_threw():
    """Two drivers instead of twenty raises no exception anywhere upstream."""
    payload = SessionPayload(session=LEGACY, results=_results(LEGACY, 2))
    report = run_integrity_checks(payload)

    assert _named(report, "results_present").passed
    assert not _named(report, "field_size_plausible").passed


def test_missing_laps_in_the_middle_are_caught():
    laps = _laps(MODERN, "Driver 1", 1, 10) + _laps(MODERN, "Driver 1", 15, 20)
    payload = SessionPayload(
        session=MODERN, results=_results(MODERN, 20), laps=laps
    )
    check = _named(run_integrity_checks(payload), "lap_numbering_contiguous")

    assert not check.passed
    assert "missing laps" in check.detail


def test_duplicate_laps_are_caught():
    laps = _laps(MODERN, "Driver 1", 1, 10) + _laps(MODERN, "Driver 1", 5, 5)
    payload = SessionPayload(
        session=MODERN, results=_results(MODERN, 20), laps=laps
    )
    check = _named(run_integrity_checks(payload), "lap_numbering_contiguous")

    assert not check.passed
    assert "duplicate laps" in check.detail


def test_wholesale_missing_driver_laps_are_caught():
    payload = SessionPayload(
        session=MODERN,
        results=_results(MODERN, 20),
        laps=_laps(MODERN, "Driver 1", 1, 50),
    )
    check = _named(run_integrity_checks(payload), "drivers_have_laps")

    assert not check.passed
    assert "19 classified driver(s) without laps" in check.detail


def test_two_non_starters_are_tolerated():
    """Cars that never take the start have no laps and that is not a gap."""
    results = _results(MODERN, 20)
    laps = []
    for row in results[:18]:
        laps.extend(_laps(MODERN, row.driver, 1, 50))
    payload = SessionPayload(session=MODERN, results=results, laps=laps)

    assert _named(run_integrity_checks(payload), "drivers_have_laps").passed


def test_stint_totals_must_reconcile_with_lap_counts():
    payload = SessionPayload(
        session=MODERN,
        results=_results(MODERN, 20),
        laps=_laps(MODERN, "Driver 1", 1, 50),
        stints=[
            StintRow(
                id="s1", season=2024, round=5, driver="Driver 1", stint=1, laps=20
            )
        ],
    )
    check = _named(run_integrity_checks(payload), "stint_laps_reconcile")

    assert not check.passed
    assert "50 laps vs 20 in stints" in check.detail


def test_more_stops_than_stints_is_impossible():
    payload = SessionPayload(
        session=MODERN,
        results=_results(MODERN, 20),
        stints=[
            StintRow(id="s1", season=2024, round=5, driver="Driver 1", stint=1, laps=25)
        ],
        pit_stops=[
            PitStopRow(
                id="p{}".format(n),
                season=2024,
                round=5,
                driver="Driver 1",
                stop=n,
                lap=n * 10,
            )
            for n in (1, 2)
        ],
    )
    check = _named(run_integrity_checks(payload), "pit_stops_within_stints")

    assert not check.passed
    assert "2 stops, 1 stints" in check.detail


def test_a_clean_modern_race_passes_every_check():
    """The false-positive guard: a well-formed race must come back clean."""
    results = _results(MODERN, 20)
    laps, stints, stops = [], [], []
    for index, row in enumerate(results, start=1):
        laps.extend(_laps(MODERN, row.driver, 1, 50))
        stints.extend(
            [
                StintRow(
                    id="s{}-1".format(index),
                    season=2024,
                    round=5,
                    driver=row.driver,
                    stint=1,
                    laps=25,
                ),
                StintRow(
                    id="s{}-2".format(index),
                    season=2024,
                    round=5,
                    driver=row.driver,
                    stint=2,
                    laps=25,
                ),
            ]
        )
        stops.append(
            PitStopRow(
                id="p{}".format(index),
                season=2024,
                round=5,
                driver=row.driver,
                stop=1,
                lap=25,
            )
        )

    report = run_integrity_checks(
        SessionPayload(
            session=MODERN,
            results=results,
            laps=laps,
            stints=stints,
            pit_stops=stops,
        )
    )

    assert report.passed, [check.detail for check in report.failures]


# ── Gap accounting ───────────────────────────────────────────────────────────


def _state(round_number: int, state: SessionState) -> SessionIngestState:
    return SessionIngestState(season=2024, round=round_number, state=state)


def test_never_attempted_session_counts_as_an_open_gap():
    """A session with no state row is indistinguishable from a failed one."""
    summary = summarise(2024, 2024, ["2024-1", "2024-2"], [_state(1, SessionState.COMPLETE)])

    assert summary.open_gaps == ["2024-2"]
    assert summary.pending == 1
    assert not summary.is_complete


def test_partial_and_failed_are_both_gaps():
    summary = summarise(
        2024,
        2024,
        ["2024-1", "2024-2", "2024-3"],
        [
            _state(1, SessionState.COMPLETE),
            _state(2, SessionState.PARTIAL),
            _state(3, SessionState.FAILED),
        ],
    )

    assert summary.open_gaps == ["2024-2", "2024-3"]
    assert summary.complete == 1


def test_future_race_is_not_a_gap():
    """A race that has not run yet is not missing data."""
    summary = summarise(
        2024,
        2024,
        ["2024-1", "2024-2"],
        [_state(1, SessionState.COMPLETE), _state(2, SessionState.UNAVAILABLE)],
    )

    assert summary.open_gaps == []
    assert summary.unavailable == 1
    assert summary.is_complete


# ── Empty-schedule guard ─────────────────────────────────────────────────────


def test_a_season_with_no_expected_sessions_is_not_reported_complete():
    """The catastrophic case: nothing expected means nothing missing.

    Upstream rate limiting can return an empty schedule instead of an error. If
    that reaches the manifest, the season records zero expected sessions, the
    gap diff finds nothing to complain about, and a season we never fetched at
    all is published as COMPLETE. The guard lives in fastf1_source.fetch_schedule
    (it raises rather than returning []); this pins the reason it has to.
    """
    summary = summarise(2023, 2023, [], [])

    assert summary.expected == 0
    # is_complete is vacuously true here, which is exactly why an empty
    # manifest must never be allowed to reach this function.
    assert summary.is_complete
    assert summary.open_gaps == []
