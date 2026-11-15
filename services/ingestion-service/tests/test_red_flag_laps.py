"""A race whose official laps stop at a red flag is finished from OpenF1.

Mexico 2026: with an F1TV login, official timing served laps 1-56 and then
nothing after the restart, though the race ran 71 laps. The lap numbering had
no holes, so the round was recorded complete. These tests pin both halves of
the fix: the merge that finishes the race, and the check that refuses to call
a short race complete.
"""

from datetime import datetime

import pandas as pd

from app.models.schemas import ExpectedSession, LapRow, ResultRow, SessionPayload
from app.services.completeness import run_integrity_checks
from app.services.fastf1_source import (
    FastF1Source,
    SessionFrames,
    classified_distance,
    laps_after_cut,
    session_t0,
    t0_from_matched_laps,
)
from app.services.openf1_source import OpenF1Unavailable
from app.services.transforms import extract_results

T0 = datetime(2026, 10, 25, 19, 0, 0)
RACE = ExpectedSession(season=2026, round=19, race_name="Mexico City Grand Prix")


def _official(number="1", code="VER", last=56, stint_at=None):
    rows = []
    for lap in range(1, last + 1):
        rows.append({
            "Driver": code, "DriverNumber": number, "LapNumber": float(lap),
            "LapTime": pd.Timedelta(seconds=80),
            "Stint": float(stint_at(lap) if stint_at else 1),
            "Compound": "MEDIUM", "PitInTime": pd.NaT, "PitOutTime": pd.NaT,
            "LapStartDate": pd.Timestamp(T0) + pd.Timedelta(seconds=80 * lap),
        })
    frame = pd.DataFrame(rows)
    for column in ("PitInTime", "PitOutTime"):
        frame[column] = pd.Series([pd.NaT] * len(frame), dtype="timedelta64[ns]")
    return frame


def _openf1(number="1", last=71, stint_at=None, pit_in_lap=None):
    rows = []
    for lap in range(1, last + 1):
        start = pd.Timestamp(T0, tz="UTC") + pd.Timedelta(seconds=80 * lap)
        pit_out = pit_in_lap is not None and lap == pit_in_lap + 1
        rows.append({
            "Driver": "VER", "DriverNumber": number, "LapNumber": lap,
            "LapTime": pd.Timedelta(seconds=81),
            "Stint": stint_at(lap) if stint_at else 1,
            "Compound": "HARD" if lap > 60 else "MEDIUM", "LapStartDate": start,
            "PitInTime": start + pd.Timedelta(seconds=78) if lap == pit_in_lap else pd.NaT,
            "PitOutTime": start + pd.Timedelta(seconds=2) if pit_out else pd.NaT,
        })
    frame = pd.DataFrame(rows)
    for column in ("PitInTime", "PitOutTime"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def test_only_laps_past_the_official_cut_are_taken():
    extra = laps_after_cut(_official(), _openf1(), T0)

    assert list(extra["LapNumber"]) == list(range(57, 72))
    assert set(extra["Driver"]) == {"VER"}


def test_pit_times_become_session_time_so_stops_pair_up():
    extra = laps_after_cut(_official(), _openf1(pit_in_lap=60), T0)

    pit_in = extra.loc[extra["LapNumber"] == 60, "PitInTime"].iloc[0]
    pit_out = extra.loc[extra["LapNumber"] == 61, "PitOutTime"].iloc[0]
    assert isinstance(pit_in, pd.Timedelta)
    assert pit_out - pit_in == pd.Timedelta(seconds=4)
    assert str(extra["LapStartDate"].dtype) == "datetime64[ns]"


def test_stint_numbers_continue_the_official_count():
    # Official: stint 2 from lap 30. OpenF1 numbers the same stint 1 because it
    # missed the first. After the cut the official count must carry on.
    official = _official(stint_at=lambda lap: 1 if lap < 30 else 2)
    theirs = _openf1(stint_at=lambda lap: 0 if lap < 30 else (1 if lap <= 60 else 2))

    extra = laps_after_cut(official, theirs, T0)

    assert set(extra.loc[extra["LapNumber"] <= 60, "Stint"]) == {2}
    assert set(extra.loc[extra["LapNumber"] > 60, "Stint"]) == {3}


def test_a_car_that_retired_before_the_cut_gets_nothing_added():
    extra = laps_after_cut(_official(last=30), _openf1(last=30), T0)
    assert extra.empty


class _FakeOpenF1:
    enabled = True

    def __init__(self, laps=None, fail=False):
        self._laps = laps if laps is not None else _openf1()
        self._fail = fail

    def find_session(self, expected, code):
        if self._fail:
            raise OpenF1Unavailable("sessions returned HTTP 401")
        return {"session_key": 1}

    def laps_frame(self, key, with_pits):
        return self._laps, {}, {}

    def weather_frame(self, key):
        return pd.DataFrame([
            {"Time": "2026-10-25T19:30:00+00:00", "AirTemp": 20.0},
            {"Time": "2026-10-25T20:40:00+00:00", "AirTemp": 21.0},
        ])

    def race_control_frame(self, key):
        return pd.DataFrame([{"Time": "2026-10-25T20:45:00+00:00", "Message": "GREEN"}])


def _frames():
    results = pd.DataFrame([{"DriverNumber": "1", "Laps": 71.0, "Position": 1.0}])
    weather = pd.DataFrame([{"Time": pd.Timedelta(minutes=60), "AirTemp": 20.0}])
    control = pd.DataFrame([
        {"Time": pd.Timestamp(T0) + pd.Timedelta(minutes=70), "Message": "RED FLAG"}
    ])
    return SessionFrames(session=RACE, results=results, laps=_official(),
                         weather=weather, race_control=control)


def test_a_short_official_race_is_finished_from_openf1():
    source = FastF1Source("unused", load_telemetry=False, openf1=_FakeOpenF1())
    frames = _frames()

    source._extend_from_openf1(RACE, frames, T0)

    assert int(frames.laps["LapNumber"].max()) == 71
    assert len(frames.laps) == 71
    # Only the weather sample after the last official one, in session time.
    assert list(frames.weather["Time"]) == [
        pd.Timedelta(minutes=60), pd.Timedelta(minutes=100)
    ]
    assert list(frames.race_control["Message"]) == ["RED FLAG", "GREEN"]


def test_openf1_locked_during_a_live_session_leaves_the_official_laps():
    source = FastF1Source("unused", load_telemetry=False, openf1=_FakeOpenF1(fail=True))
    frames = _frames()

    source._extend_from_openf1(RACE, frames, T0)

    assert int(frames.laps["LapNumber"].max()) == 56


def test_a_full_distance_race_is_left_alone():
    source = FastF1Source("unused", load_telemetry=False, openf1=_FakeOpenF1(fail=True))
    frames = _frames()
    frames.laps = _official(last=71)

    source._extend_from_openf1(RACE, frames, T0)

    assert len(frames.laps) == 71


def test_classified_distance_reads_the_winner_laps():
    assert classified_distance(pd.DataFrame([{"Laps": 71.0}, {"Laps": 70.0}])) == 71
    assert classified_distance(pd.DataFrame([{"Position": 1}])) == 0


def test_results_carry_laps_completed():
    frame = pd.DataFrame([{
        "FullName": "Max Verstappen", "TeamName": "Red Bull Racing", "Position": 1.0,
        "Points": 25.0, "GridPosition": 1.0, "ClassifiedPosition": "1",
        "Status": "Finished", "Laps": 71.0, "DriverNumber": "1", "Abbreviation": "VER",
    }])
    rows = extract_results(RACE, frame)
    assert rows[0].laps_completed == 71


def _payload(held):
    results = [
        ResultRow(id="a", season=2026, round=19, driver="Winner", position=1,
                  points=25.0, classified_position="1", status="Finished",
                  laps_completed=71),
        ResultRow(id="b", season=2026, round=19, driver="Second", position=2,
                  points=18.0, classified_position="2", status="Finished",
                  laps_completed=71),
    ]
    laps = [
        LapRow(id="w{}".format(n), season=2026, round=19, driver="Winner", lap=n)
        for n in range(1, held + 1)
    ]
    return SessionPayload(session=RACE, results=results, laps=laps)


def _check(report):
    return next(c for c in report.checks if c.name == "laps_reach_distance")


def test_laps_that_stop_before_the_race_distance_are_not_complete():
    check = _check(run_integrity_checks(_payload(56)))
    assert not check.passed
    assert "56 of 71" in check.detail


def test_laps_that_reach_the_race_distance_pass():
    assert _check(run_integrity_checks(_payload(71))).passed


def test_an_unknown_lap_count_cannot_fail_the_check():
    payload = _payload(10)
    for row in payload.results:
        row.laps_completed = 0
    assert _check(run_integrity_checks(payload)).passed


class _NoTelemetrySession:
    """FastF1 3.9 raises on ``t0_date`` when car data was not loaded."""

    @property
    def t0_date(self):
        raise RuntimeError("The data you are trying to access has not been loaded yet")


def test_t0_comes_from_the_laps_when_telemetry_was_not_loaded():
    laps = _official()
    laps["LapStartTime"] = [pd.Timedelta(seconds=80 * lap) for lap in range(1, len(laps) + 1)]

    assert session_t0(_NoTelemetrySession(), laps) == T0


def test_t0_is_none_when_nothing_can_place_it():
    assert session_t0(_NoTelemetrySession(), pd.DataFrame()) is None


def _with_session_times(frame):
    frame = frame.copy()
    frame["LapStartTime"] = [pd.Timedelta(seconds=80 * int(n)) for n in frame["LapNumber"]]
    frame["LapStartDate"] = pd.NaT  # FastF1 leaves it empty without telemetry
    return frame


def test_t0_from_laps_both_feeds_have():
    assert t0_from_matched_laps(_with_session_times(_official()), _openf1()) == T0


def test_a_short_race_is_finished_even_without_telemetry_t0():
    source = FastF1Source("unused", load_telemetry=False, openf1=_FakeOpenF1())
    frames = _frames()
    frames.laps = _with_session_times(frames.laps)

    source._extend_from_openf1(RACE, frames, None)

    assert int(frames.laps["LapNumber"].max()) == 71


def test_a_car_that_retired_long_before_the_cut_gets_no_phantom_lap():
    # Stroll, Mexico 2026: official laps 1-5, OpenF1 adds an untimed lap 6.
    official = pd.concat([
        _with_session_times(_official()),
        _with_session_times(_official(number="18", code="STR", last=5)),
    ], ignore_index=True)
    theirs = pd.concat([_openf1(), _openf1(number="18", last=6)], ignore_index=True)

    extra = laps_after_cut(official, theirs, T0)

    assert set(extra["DriverNumber"]) == {"1"}
    assert list(extra["LapNumber"]) == list(range(57, 72))
