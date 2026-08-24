"""Transform-layer tests.

The pit-stop cases matter most: FastF1 splits a single stop across two lap rows,
and the naive same-row reading silently produces zero stops rather than failing
loudly. These tests pin that behaviour down.
"""

import pandas as pd
import pytest

from app.models.schemas import ExpectedSession
from app.services.transforms import (
    build_driver_name_map,
    extract_laps,
    extract_pit_stops,
    extract_results,
    extract_stints,
    safe_float,
    safe_int,
    safe_text,
    stable_id,
    to_seconds,
)

SESSION = ExpectedSession(
    season=2024,
    round=5,
    race_name="Test Grand Prix",
    circuit="Testville",
    race_date="2024-05-05",
)


# ── Coercion ─────────────────────────────────────────────────────────────────


def test_safe_helpers_absorb_nan_and_none():
    assert safe_float(float("nan")) == 0.0
    assert safe_float(None, 1.5) == 1.5
    assert safe_float("2.5") == 2.5
    assert safe_int(float("nan"), 7) == 7
    assert safe_int(None, 3) == 3
    assert safe_int("4") == 4
    assert safe_text(None, "fallback") == "fallback"
    assert safe_text("   ", "fallback") == "fallback"
    assert safe_text("  Lando  ") == "Lando"


def test_to_seconds_handles_timedelta():
    assert to_seconds(pd.Timedelta(seconds=25.4)) == pytest.approx(25.4)
    assert to_seconds(None) == 0.0


def test_stable_id_is_deterministic_and_key_sensitive():
    first = stable_id(2024, 5, "Lando Norris", "McLaren")
    assert first == stable_id(2024, 5, "Lando Norris", "McLaren")
    assert first != stable_id(2024, 6, "Lando Norris", "McLaren")


# ── Results ──────────────────────────────────────────────────────────────────


def _results_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Abbreviation": "VER",
                "FullName": "Max Verstappen",
                "TeamName": "Red Bull Racing",
                "Position": 1.0,
                "ClassifiedPosition": "1",
                "Points": 25.0,
            },
            {
                "Abbreviation": "NOR",
                "FullName": "Lando Norris",
                "TeamName": "McLaren",
                "Position": 2.0,
                "ClassifiedPosition": "2",
                "Points": 18.0,
            },
        ]
    )


def test_extract_results_maps_fields_and_assigns_stable_ids():
    rows = extract_results(SESSION, _results_frame())

    assert [row.driver for row in rows] == ["Max Verstappen", "Lando Norris"]
    assert rows[0].position == 1
    assert rows[0].points == 25.0
    assert rows[0].team == "Red Bull Racing"
    assert rows[0].circuit == "Testville"
    assert rows[0].id == stable_id(2024, 5, "Max Verstappen", "Red Bull Racing")


def test_extract_results_on_empty_frame_returns_nothing():
    assert extract_results(SESSION, pd.DataFrame()) == []
    assert extract_results(SESSION, None) == []


def test_driver_name_map_resolves_codes_to_full_names():
    mapping = build_driver_name_map(_results_frame())
    assert mapping == {"VER": "Max Verstappen", "NOR": "Lando Norris"}


# ── Laps ─────────────────────────────────────────────────────────────────────


def test_extract_laps_resolves_driver_code_to_full_name():
    laps = pd.DataFrame(
        [
            {
                "Driver": "VER",
                "LapNumber": 1,
                "LapTime": pd.Timedelta(seconds=92.4),
                "Compound": "MEDIUM",
                "Stint": 1,
            }
        ]
    )
    rows = extract_laps(SESSION, laps, {"VER": "Max Verstappen"})

    assert len(rows) == 1
    assert rows[0].driver == "Max Verstappen"
    assert rows[0].lap == 1
    assert rows[0].lap_time_seconds == pytest.approx(92.4)
    assert rows[0].compound == "MEDIUM"


def test_extract_laps_falls_back_to_code_when_name_unknown():
    laps = pd.DataFrame(
        [{"Driver": "XXX", "LapNumber": 1, "LapTime": None, "Compound": None, "Stint": 1}]
    )
    rows = extract_laps(SESSION, laps, {})

    # An unmapped driver must still be recorded — dropping it would silently
    # lose data, which is exactly what this pipeline must not do.
    assert len(rows) == 1
    assert rows[0].driver == "XXX"
    assert rows[0].compound == "Unknown"


# ── Pit stops ────────────────────────────────────────────────────────────────


def test_pit_stop_pairs_entry_lap_with_following_lap_exit():
    """The real FastF1 shape: PitInTime on lap N, PitOutTime on lap N+1."""
    laps = pd.DataFrame(
        [
            {
                "Driver": "VER",
                "LapNumber": 10,
                "PitInTime": pd.Timedelta(seconds=3000),
                "PitOutTime": pd.NaT,
            },
            {
                "Driver": "VER",
                "LapNumber": 11,
                "PitInTime": pd.NaT,
                "PitOutTime": pd.Timedelta(seconds=3025),
            },
        ]
    )
    rows = extract_pit_stops(SESSION, laps, {"VER": "Max Verstappen"})

    assert len(rows) == 1
    assert rows[0].driver == "Max Verstappen"
    assert rows[0].stop == 1
    assert rows[0].lap == 10
    assert rows[0].duration_seconds == pytest.approx(25.0)


def test_pit_stops_number_sequentially_per_driver():
    laps = pd.DataFrame(
        [
            {"Driver": "VER", "LapNumber": 10, "PitInTime": pd.Timedelta(seconds=3000), "PitOutTime": pd.NaT},
            {"Driver": "VER", "LapNumber": 11, "PitInTime": pd.NaT, "PitOutTime": pd.Timedelta(seconds=3022)},
            {"Driver": "VER", "LapNumber": 30, "PitInTime": pd.Timedelta(seconds=4500), "PitOutTime": pd.NaT},
            {"Driver": "VER", "LapNumber": 31, "PitInTime": pd.NaT, "PitOutTime": pd.Timedelta(seconds=4524)},
            {"Driver": "NOR", "LapNumber": 12, "PitInTime": pd.Timedelta(seconds=3100), "PitOutTime": pd.NaT},
            {"Driver": "NOR", "LapNumber": 13, "PitInTime": pd.NaT, "PitOutTime": pd.Timedelta(seconds=3123)},
        ]
    )
    rows = extract_pit_stops(SESSION, laps, {"VER": "Max Verstappen", "NOR": "Lando Norris"})

    ver = [r for r in rows if r.driver == "Max Verstappen"]
    nor = [r for r in rows if r.driver == "Lando Norris"]
    assert [r.stop for r in ver] == [1, 2]
    assert [r.stop for r in nor] == [1]
    assert ver[1].duration_seconds == pytest.approx(24.0)


def test_pit_stop_dropped_when_exit_never_recorded():
    """A retirement in the pits leaves no exit — it is not a completed stop."""
    laps = pd.DataFrame(
        [
            {
                "Driver": "VER",
                "LapNumber": 10,
                "PitInTime": pd.Timedelta(seconds=3000),
                "PitOutTime": pd.NaT,
            }
        ]
    )
    assert extract_pit_stops(SESSION, laps, {"VER": "Max Verstappen"}) == []


def test_pit_stop_ignores_non_positive_duration():
    laps = pd.DataFrame(
        [
            {"Driver": "VER", "LapNumber": 10, "PitInTime": pd.Timedelta(seconds=3000), "PitOutTime": pd.NaT},
            {"Driver": "VER", "LapNumber": 11, "PitInTime": pd.NaT, "PitOutTime": pd.Timedelta(seconds=2990)},
        ]
    )
    assert extract_pit_stops(SESSION, laps, {"VER": "Max Verstappen"}) == []


def test_pit_stops_on_frame_without_pit_columns():
    laps = pd.DataFrame([{"Driver": "VER", "LapNumber": 1}])
    assert extract_pit_stops(SESSION, laps, {"VER": "Max Verstappen"}) == []


# ── Stints ───────────────────────────────────────────────────────────────────


def test_extract_stints_counts_laps_per_compound():
    laps = pd.DataFrame(
        [
            {"Driver": "VER", "Stint": 1, "Compound": "MEDIUM"},
            {"Driver": "VER", "Stint": 1, "Compound": "MEDIUM"},
            {"Driver": "VER", "Stint": 2, "Compound": "HARD"},
        ]
    )
    rows = extract_stints(SESSION, laps, {"VER": "Max Verstappen"})

    by_stint = {row.stint: row for row in rows}
    assert by_stint[1].laps == 2
    assert by_stint[1].compound == "MEDIUM"
    assert by_stint[2].laps == 1
    assert by_stint[2].compound == "HARD"
    assert all(row.driver == "Max Verstappen" for row in rows)
