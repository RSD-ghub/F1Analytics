"""OpenF1 as the lap source when F1's live timing returns nothing.

The frames must come out shaped like FastF1's, because the same transforms
read them: a pit stop has to pair entry on the in-lap with exit on the next
lap, compound and stint have to be joined on from the stint ranges, and the
driver has to carry our spelling, not OpenF1's capitals.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.models.schemas import ExpectedSession
from app.services import transforms
from app.services.fastf1_source import FastF1Source, SessionFrames
from app.services.openf1_source import OpenF1Client, OpenF1Unavailable


class _Response:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Http:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, **_):
        path = url.rsplit("/", 1)[-1]
        self.calls.append((path, dict(params or {})))
        return _Response(self.routes.get(path, []))


SESSIONS = [
    # A race our calendar does not have, a week earlier: must not be picked.
    {"session_key": 1, "meeting_key": 10, "session_name": "Race",
     "date_start": "2026-10-18T19:00:00+00:00"},
    {"session_key": 11397, "meeting_key": 1298, "session_name": "Practice 1",
     "date_start": "2026-10-30T18:30:00+00:00"},
    {"session_key": 11398, "meeting_key": 1298, "session_name": "Practice 2",
     "date_start": "2026-10-30T22:00:00+00:00"},
    {"session_key": 11404, "meeting_key": 1298, "session_name": "Race",
     "date_start": "2026-11-01T20:00:00+00:00"},
]

ROUTES = {
    "sessions": SESSIONS,
    "drivers": [
        {"driver_number": 4, "name_acronym": "NOR", "first_name": "Lando", "last_name": "NORRIS"},
        {"driver_number": 12, "name_acronym": "ANT", "first_name": "Andrea Kimi", "last_name": "ANTONELLI"},
    ],
    "laps": [
        {"driver_number": 4, "lap_number": n, "lap_duration": 80.0 + n / 10,
         "duration_sector_1": 25.0, "duration_sector_2": 30.0, "duration_sector_3": 25.0,
         "i1_speed": 300, "i2_speed": 280, "st_speed": 320,
         "is_pit_out_lap": n == 4, "date_start": "2026-11-01T20:{:02d}:00+00:00".format(n)}
        for n in range(1, 7)
    ] + [
        {"driver_number": 12, "lap_number": n, "lap_duration": 81.0,
         "is_pit_out_lap": False, "date_start": "2026-11-01T20:{:02d}:00+00:00".format(n)}
        for n in range(1, 7)
    ],
    "stints": [
        {"driver_number": 4, "stint_number": 1, "lap_start": 1, "lap_end": 3,
         "compound": "MEDIUM", "tyre_age_at_start": 0},
        # Lap 6 is left uncovered, as OpenF1 sometimes does.
        {"driver_number": 4, "stint_number": 2, "lap_start": 4, "lap_end": 5,
         "compound": "HARD", "tyre_age_at_start": 0},
        {"driver_number": 12, "stint_number": 1, "lap_start": 1, "lap_end": 6,
         "compound": "HARD", "tyre_age_at_start": 2},
    ],
    "pit": [
        {"driver_number": 4, "lap_number": 3, "pit_duration": 23.96,
         "date": "2026-11-01T20:03:50+00:00"},
    ],
    "weather": [
        {"date": "2026-11-01T20:00:00+00:00", "air_temperature": 22.1,
         "track_temperature": 40.2, "humidity": 41.0, "rainfall": 0},
    ],
    "race_control": [
        {"date": "2026-11-01T20:00:00+00:00", "lap_number": 1,
         "category": "Flag", "message": "GREEN LIGHT - PIT EXIT OPEN"},
    ],
}

MEXICO = ExpectedSession(
    season=2026, round=19, race_name="Mexico City Grand Prix",
    session_start_utc=datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc),
)


def _client(routes=ROUTES):
    http = _Http(routes)
    return OpenF1Client("https://openf1.test/v1", http=http, min_interval=0), http


def test_the_race_is_matched_by_date_not_by_position():
    client, _ = _client()
    assert client.find_session(MEXICO, "R")["session_key"] == 11404
    assert client.find_session(MEXICO, "FP2")["session_key"] == 11398


def test_a_race_with_no_session_nearby_is_not_guessed():
    client, _ = _client()
    far = MEXICO.model_copy(update={
        "session_start_utc": datetime(2026, 12, 6, 13, 0, tzinfo=timezone.utc)})
    with pytest.raises(OpenF1Unavailable):
        client.find_session(far, "R")


def test_seasons_before_openf1_are_not_asked_for():
    client, http = _client()
    with pytest.raises(OpenF1Unavailable):
        client.find_session(MEXICO.model_copy(update={"season": 2019}), "R")
    assert http.calls == []


def test_race_laps_become_the_same_rows_fastf1_would_give():
    client, _ = _client()
    laps, names, numbers = client.laps_frame(11404, with_pits=True)

    rows = transforms.extract_laps(MEXICO, laps, {})
    norris = {r.lap: r for r in rows if r.driver == "NOR"}
    assert norris[2].compound == "MEDIUM" and norris[2].stint == 1
    assert norris[5].compound == "HARD" and norris[5].stint == 2
    assert norris[2].sector1_seconds == 25.0
    assert norris[2].speed_trap_kph == 320

    stops = transforms.extract_pit_stops(MEXICO, laps, {})
    assert [(s.driver, s.lap, round(s.duration_seconds, 2)) for s in stops] == [
        ("NOR", 3, 23.96)]

    stints = transforms.extract_stints(MEXICO, laps, {})
    assert sorted((s.driver, s.stint, s.compound, s.laps) for s in stints) == [
        ("ANT", 1, "HARD", 6), ("NOR", 1, "MEDIUM", 3), ("NOR", 2, "HARD", 3)]
    assert numbers == {"NOR": 4, "ANT": 12}
    assert names["NOR"] == "Lando Norris"


def test_practice_marks_pit_laps_so_pace_skips_them():
    client, _ = _client()
    laps, _, _ = client.laps_frame(11398, with_pits=False)
    norris = laps[laps["Driver"] == "NOR"].set_index("LapNumber")
    assert pd.notna(norris.loc[4, "PitOutTime"])
    assert pd.notna(norris.loc[3, "PitInTime"])
    assert pd.isna(norris.loc[2, "PitInTime"]) and pd.isna(norris.loc[2, "PitOutTime"])


def test_an_empty_race_is_filled_with_our_spelling_of_each_driver():
    client, _ = _client()
    source = FastF1Source(cache_dir="/nonexistent", openf1=client)
    source.use_driver_names(2026, {12: "Kimi Antonelli"})
    results = pd.DataFrame([
        {"DriverNumber": "4", "Abbreviation": "NOR", "FullName": "Lando Norris"},
        {"DriverNumber": "12", "Abbreviation": "ANT", "FullName": "Kimi Antonelli"},
    ])
    frames = SessionFrames(session=MEXICO, results=results)

    source._fill_from_openf1(MEXICO, frames)

    assert set(frames.laps["Driver"]) == {"Lando Norris", "Kimi Antonelli"}
    assert len(frames.weather) == 1 and len(frames.race_control) == 1
    names = transforms.build_driver_name_map(results)
    assert {r.driver for r in transforms.extract_laps(MEXICO, frames.laps, names)} == {
        "Lando Norris", "Kimi Antonelli"}


def test_openf1_being_down_leaves_the_frames_empty():
    class _Down:
        def get(self, *a, **k):
            raise ConnectionError("no route")

    source = FastF1Source(
        cache_dir="/nonexistent",
        openf1=OpenF1Client("https://openf1.test/v1", http=_Down(), min_interval=0),
    )
    frames = SessionFrames(session=MEXICO)
    source._fill_from_openf1(MEXICO, frames)
    assert frames.laps.empty


def test_requests_are_spaced_under_the_free_limit():
    waits = []
    now = [0.0]
    client = OpenF1Client(
        "https://openf1.test/v1", http=_Http(ROUTES), min_interval=2.1,
        clock=lambda: now[0], sleep=lambda s: waits.append(s),
    )
    client.get("drivers")
    now[0] = 0.5
    client.get("laps")
    assert waits and abs(waits[0] - 1.6) < 1e-9


def test_a_lap_no_stint_covers_joins_the_nearest_stint():
    client, _ = _client()
    laps, _, _ = client.laps_frame(11404, with_pits=True)
    last = laps[(laps["Driver"] == "NOR") & (laps["LapNumber"] == 6)].iloc[0]
    assert (last["Compound"], last["Stint"]) == ("HARD", 2)
    assert "Unknown" not in set(laps["Compound"])
