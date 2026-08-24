"""Practice long-run pace — the one current-weekend signal pre-quali may use.

Free practice runs before qualifying, so this weekend's FP2 leaks nothing into a
pre-quali forecast. That is what makes it valuable: it is measured *after*
upgrades are fitted, and it is the objective version of what paddock news about
upgrades only gossips about.

The tests that matter here are the session-selection ones. Pooling lap times
across FP1 and FP3 compares drivers on different fuel loads, tyres and track
conditions, and produces a confident-looking number that means nothing.
"""

import pytest

from app.models.schemas import LockWindow
from app.services import practice_signal
from app.services.features import build_snapshot
from app.services.ingestion_client import PracticePace, RaceResult

DRIVERS = ["Alpha", "Bravo", "Charlie", "Delta"]


def _pace(session, times, season=2026, rnd=6):
    return [
        PracticePace(season=season, round=rnd, session_name=session, driver=d,
                     long_run_seconds=t, long_run_laps=10, best_lap_seconds=t - 5)
        for d, t in zip(DRIVERS, times)
    ]


# ── Gap computation ──────────────────────────────────────────────────────────


def test_gaps_are_relative_to_the_session_best():
    gaps = practice_signal.gaps_for_round(_pace("FP2", [90.0, 90.9, 91.8, 92.7]), 2026, 6)

    assert gaps["Alpha"] == pytest.approx(0.0)
    assert gaps["Bravo"] == pytest.approx(0.01, abs=1e-6)
    assert gaps["Delta"] == pytest.approx(0.03, abs=1e-6)


def test_fp2_is_preferred_over_fp3():
    """FP2 is the race-simulation session; FP3 is qualifying prep on low fuel.

    Preferring the later session would look tidier and measure the wrong thing.
    """
    rows = _pace("FP3", [95.0, 90.0, 90.0, 90.0]) + _pace("FP2", [90.0, 95.0, 95.0, 95.0])
    gaps = practice_signal.gaps_for_round(rows, 2026, 6)

    # FP2 says Alpha is fastest; FP3 said the opposite.
    assert gaps["Alpha"] == pytest.approx(0.0)
    assert gaps["Bravo"] > 0.05


def test_fp3_is_used_when_fp2_is_absent():
    """A washed-out FP2 should fall through, not blank the feature."""
    gaps = practice_signal.gaps_for_round(_pace("FP3", [90.0, 91.0, 92.0, 93.0]), 2026, 6)
    assert gaps["Alpha"] == pytest.approx(0.0)


def test_sessions_are_never_pooled():
    """Averaging across sessions compares incomparable runs.

    With FP1 slow and FP3 fast, a pooled reference would put every FP1 lap
    enormous distances off the best and invent a spread that does not exist.
    """
    rows = _pace("FP1", [100.0, 101.0, 102.0, 103.0]) + _pace("FP3", [90.0, 90.1, 90.2, 90.3])
    gaps = practice_signal.gaps_for_round(rows, 2026, 6)

    assert max(gaps.values()) < 0.01, "gap spread implies sessions were pooled"


def test_runs_without_enough_laps_are_ignored():
    rows = [
        PracticePace(season=2026, round=6, session_name="FP2", driver="Alpha",
                     long_run_seconds=90.0, long_run_laps=0),
    ]
    assert practice_signal.gaps_for_round(rows, 2026, 6) == {}


def test_another_weekend_is_not_borrowed():
    rows = _pace("FP2", [90.0, 91.0, 92.0, 93.0], rnd=5)
    assert practice_signal.gaps_for_round(rows, 2026, 6) == {}


def test_no_practice_data_yields_no_gaps():
    assert practice_signal.gaps_for_round([], 2026, 6) == {}


# ── Integration with the feature builder ─────────────────────────────────────


def _history():
    rows = []
    for rnd in range(1, 6):
        for i, driver in enumerate(DRIVERS):
            rows.append(RaceResult(
                season=2026, round=rnd, driver=driver, team="T%d" % i,
                position=i + 1, classified_position=str(i + 1), status="Finished",
                points=float(25 - i * 5), grid_position=i + 1,
            ))
    return rows


def test_pre_quali_may_use_this_weekends_practice():
    """The point of the feature. Practice precedes qualifying, so this is not
    leakage — it is the only current-weekend pace the pre-quali model can see."""
    snapshot = build_snapshot(
        _history(), 2026, 6, LockWindow.PRE_QUALI,
        practice=_pace("FP2", [90.0, 91.0, 92.0, 93.0]),
    )
    gaps = {d.driver: d.practice_long_run_gap_pct for d in snapshot.drivers}

    assert gaps["Alpha"] == pytest.approx(0.0)
    assert gaps["Delta"] > gaps["Bravo"] > gaps["Alpha"]


def test_practice_changes_the_snapshot():
    """Guard against the feature being silently inert, as the era term was."""
    without = build_snapshot(_history(), 2026, 6, LockWindow.PRE_QUALI)
    with_practice = build_snapshot(
        _history(), 2026, 6, LockWindow.PRE_QUALI,
        practice=_pace("FP2", [90.0, 91.0, 92.0, 93.0]),
    )

    assert without.snapshot_id != with_practice.snapshot_id


def test_a_driver_absent_from_practice_gets_the_neutral_value():
    """Neutral, not zero — zero would read as fastest in the session."""
    partial = _pace("FP2", [90.0, 91.0, 92.0, 93.0])[:2]
    snapshot = build_snapshot(
        _history(), 2026, 6, LockWindow.PRE_QUALI, practice=partial
    )
    missing = next(d for d in snapshot.drivers if d.driver == "Charlie")

    assert missing.practice_long_run_gap_pct == practice_signal.NEUTRAL_GAP_PCT
    assert missing.practice_long_run_gap_pct > 0
