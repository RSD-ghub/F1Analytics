"""The live feed: FastF1's recording turned into lap rows as the race runs."""

import asyncio
import json

import pytest

from app.services import live_feed
from app.services.live_timing import LiveTiming, lap_seconds, merge, parse_line


def _initial(topic, data):
    # The subscription's answer: the topic's whole state as a JSON string.
    return str([topic, json.dumps(data), ""])


def _update(topic, data, second):
    return str([topic, data, "2026-11-22T06:{:02d}:{:02d}.1234567Z".format(second // 60 % 60,
                                                                            second % 60)])


def recording():
    """Two cars, five laps. Car 4 pits on lap 3 for hards; car 81 stops on lap 4."""
    lines = [
        _initial("DriverList", {"4": {"Tla": "NOR", "FullName": "Lando NORRIS"},
                                "81": {"Tla": "PIA", "FirstName": "Oscar",
                                       "LastName": "Piastri"}}),
        _initial("TimingAppData", {"Lines": {
            "4": {"Stints": [{"Compound": "MEDIUM", "New": "true"}]},
            "81": {"Stints": [{"Compound": "MEDIUM", "New": "true"}]}}}),
        _initial("TimingData", {"Lines": {"4": {"Position": "1"}, "81": {"Position": "2"}}}),
        _initial("LapCount", {"CurrentLap": 1, "TotalLaps": 5}),
        _initial("SessionInfo", {"Meeting": {"Name": "Las Vegas Grand Prix"},
                                 "Type": "Race", "Name": "Race"}),
        _initial("RaceControlMessages", {"Messages": [
            {"Category": "Flag", "Message": "GREEN LIGHT - PIT EXIT OPEN"}]}),
        _initial("CarData.z", "compressed"),
        _update("SessionStatus", {"Status": "Started"}, 0),
    ]
    second = 10
    for lap in range(1, 6):
        for car, base in (("4", 95.0), ("81", 95.5)):
            if car == "81" and lap >= 4:
                continue
            second += 1
            change = {"NumberOfLaps": lap,
                      "LastLapTime": {"Value": "1:{:06.3f}".format(base - 60 + lap * 0.1)}}
            lines.append(_update("TimingData", {"Lines": {car: change}}, second))
        if lap == 2:
            lines.append(_update("TimingData", {"Lines": {"4": {"InPit": True}}}, second + 1))
            lines.append(_update("RaceControlMessages", {"Messages": {"1": {
                "Category": "SafetyCar", "Message": "VIRTUAL SAFETY CAR DEPLOYED",
                "Lap": 3}}}, second + 1))
        if lap == 3:
            lines.append(_update("TimingAppData", {"Lines": {"4": {"Stints": {
                "1": {"Compound": "HARD", "New": "true"}}}}}, second + 1))
            lines.append(_update("TimingData", {"Lines": {"4": {"InPit": False,
                                                                "PitOut": True}}}, second + 1))
            lines.append(_update("TimingData", {"Lines": {"81": {"Retired": True}}},
                                 second + 2))
        second += 80
    lines.append(_update("SessionStatus", {"Status": "Finished"}, second))
    return lines


def test_parse_line_reads_both_shapes_and_skips_compressed():
    assert parse_line(_initial("LapCount", {"TotalLaps": 50}))[:2] == (
        "LapCount", {"TotalLaps": 50})
    topic, data, stamp = parse_line(_update("TimingData", {"Lines": {"1": {"InPit": True}}}, 5))
    assert topic == "TimingData" and data["Lines"]["1"]["InPit"] is True
    assert stamp.startswith("2026-11-22T06:00:05")
    assert parse_line(_initial("CarData.z", "abc")) is None
    assert parse_line("not a line") is None


def test_merge_turns_lists_into_index_keyed_dicts():
    base = merge(None, {"Stints": [{"Compound": "SOFT"}]})
    after = merge(base, {"Stints": {"1": {"Compound": "HARD"}, "0": {"TotalLaps": 12}}})
    assert after == {"Stints": {"0": {"Compound": "SOFT", "TotalLaps": 12},
                                "1": {"Compound": "HARD"}}}


def test_lap_seconds():
    assert lap_seconds("1:23.456") == pytest.approx(83.456)
    assert lap_seconds("83.4") == pytest.approx(83.4)
    assert lap_seconds("") == 0.0 and lap_seconds(None) == 0.0


def test_a_recorded_race_becomes_lap_rows():
    timing = LiveTiming(2026, 22, names={81: "Oscar Piastri"})
    timing.feed_lines(recording())
    snap = timing.snapshot()
    assert snap["lap"] == 5 and snap["total_laps"] == 5 and snap["finished"]
    assert snap["session"]["meeting"] == "Las Vegas Grand Prix"
    norris = [r for r in snap["laps"] if r["driver"] == "Lando NORRIS"]
    assert [r["lap"] for r in norris] == [1, 2, 3, 4, 5]
    assert norris[0]["lap_time_seconds"] == pytest.approx(95.1)
    # In-lap still on mediums, then the new stint.
    assert [(r["compound"], r["stint"]) for r in norris] == [
        ("MEDIUM", 1), ("MEDIUM", 1), ("MEDIUM", 1), ("HARD", 2), ("HARD", 2)]
    assert snap["pit_stops"] == [{"season": 2026, "round": 22, "driver": "Lando NORRIS",
                                  "stop": 1, "lap": 3}]
    assert snap["retired"] == ["Oscar Piastri"]
    assert len([r for r in snap["laps"] if r["driver"] == "Oscar Piastri"]) == 3
    assert {"category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED", "lap": 3,
            "season": 2026, "round": 22} in snap["race_control"]
    assert snap["unnamed_cars"] == ["4"]


def test_a_lap_time_arriving_late_fills_its_lap():
    timing = LiveTiming(2026, 22)
    timing.apply("TimingData", {"Lines": {"1": {"NumberOfLaps": 1}}})
    timing.apply("TimingData", {"Lines": {"1": {"LastLapTime": {"Value": "1:40.000"}}}})
    timing.apply("TimingData", {"Lines": {"1": {"NumberOfLaps": 3,
                                                "LastLapTime": {"Value": "1:35.000"}}}})
    rows = timing.laps["1"]
    assert rows[1]["lap_time_seconds"] == 100.0
    assert rows[2]["lap_time_seconds"] == 0.0     # skipped by the feed: no time
    assert rows[3]["lap_time_seconds"] == 95.0


def test_replaying_a_recording_through_the_feed(tmp_path):
    (tmp_path / "2026-22-test.txt").write_text("\n".join(recording()) + "\n")
    feed = live_feed.LiveFeed(str(tmp_path))

    async def run():
        await feed.start(2026, 22, {4: "Lando Norris"}, recording="2026-22-test.txt")
        await asyncio.wait_for(feed._task, 5)
        return feed.status(), feed.snapshot()

    status, snap = asyncio.run(run())
    assert status["mode"] == "replay" and not status["running"] and status["lap"] == 5
    assert {r["driver"] for r in snap["laps"]} == {"Lando Norris", "Oscar Piastri"}
    assert feed.recordings() == ["2026-22-test.txt"]


def test_tailing_a_growing_recording(tmp_path, monkeypatch):
    path = tmp_path / "live.txt"
    lines = recording()
    path.write_text("\n".join(lines[:12]) + "\n" + lines[12][:20])  # a half-written line
    feed = live_feed.LiveFeed(str(tmp_path))
    feed.timing = LiveTiming(2026, 22)
    monkeypatch.setattr(live_feed, "TAIL_INTERVAL", 0.01)

    async def run():
        task = asyncio.create_task(feed._tail(str(path)))
        await asyncio.sleep(0.1)
        early = feed.timing.lap()
        with open(path, "a") as handle:
            handle.write(lines[12][20:] + "\n" + "\n".join(lines[13:]) + "\n")
        await asyncio.sleep(0.1)
        task.cancel()
        return early, feed.timing.lap()

    early, late = asyncio.run(run())
    assert early < late == 5


@pytest.mark.parametrize("name", ["../secret.txt", "", ".hidden.txt", "missing.txt"])
def test_only_recordings_in_the_folder_can_be_replayed(tmp_path, name):
    with pytest.raises(live_feed.LiveFeedError):
        live_feed.safe_recording(str(tmp_path), name)


def test_recording_live_needs_f1tv(tmp_path, monkeypatch):
    # FastF1's --no-auth path crashes against signalrcore 1.0.2, so without
    # the token the feed says why instead of starting a recorder that dies.
    monkeypatch.setattr(live_feed, "f1tv_connected", lambda: False)
    feed = live_feed.LiveFeed(str(tmp_path))
    with pytest.raises(live_feed.LiveFeedError, match="connect F1TV"):
        asyncio.run(feed.start(2026, 21, {}))
    assert feed._process is None
