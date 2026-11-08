"""The comparison half of scripts/fia_check.py. No network, no Mongo, no fia-doc.

Rows are cut from the real Mexico City 2026 run: fia-doc's output on the FIA
PDFs against what we store.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fia_check  # noqa: E402

CARS = {81: "Oscar PIASTRI", 41: "Arvid LINDBLAD", 27: "Nico HULKENBERG"}
OURS = ["Oscar Piastri", "Arvid Lindblad", "Nico Hülkenberg"]


def drivers():
    mapping, unmatched = fia_check.car_drivers(CARS, OURS)
    assert unmatched == []
    return mapping


def test_lap_seconds_reads_lap_times_and_rejects_times_of_day():
    assert fia_check.lap_seconds("1:22.497") == 82.497
    assert fia_check.lap_seconds("40:33.356") == 2433.356
    assert fia_check.lap_seconds("14:04:55") is None
    assert fia_check.lap_seconds("Championship") is None


def test_car_mapping_ignores_accents_and_case():
    assert drivers()[27] == "Nico Hülkenberg"


def test_car_mapping_leaves_ambiguous_surnames_unmatched():
    mapping, unmatched = fia_check.car_drivers({1: "A SMITH"}, ["Al Smith", "Bo Smith"])
    assert mapping == {} and unmatched == [1]


def test_laps_match_within_a_hundredth_and_lap_one_is_counted_apart():
    fia = [
        {"car_no": 81, "lap": 1, "lap_time": "1:29.100", "position": 12},
        {"car_no": 81, "lap": 2, "lap_time": "1:22.497", "position": 12},
    ]
    ours = [
        {"driver": "Oscar Piastri", "lap": 1, "lap_time_seconds": 89.1, "position": 12},
        {"driver": "Oscar Piastri", "lap": 2, "lap_time_seconds": 82.5, "position": 12},
    ]
    times, lap_one, positions = fia_check.compare_laps(fia, ours, drivers())["checks"]
    assert (times["compared"], times["matched"]) == (1, 1)
    assert (lap_one["compared"], lap_one["matched"]) == (1, 1)
    assert positions["matched"] == 2


def test_a_lap_off_by_more_than_the_tolerance_is_a_mismatch():
    fia = [{"car_no": 81, "lap": 2, "lap_time": "1:22.497", "position": 7}]
    ours = [{"driver": "Oscar Piastri", "lap": 2, "lap_time_seconds": 82.463, "position": 8}]
    times, _, positions = fia_check.compare_laps(fia, ours, drivers())["checks"]
    assert times["mismatches"][0]["diff"] == 0.034
    assert positions["mismatches"][0] == {"driver": "Oscar Piastri", "lap": 2, "fia": 7, "ours": 8}


def test_red_flag_zero_laps_are_flagged_not_compared():
    fia = [{"car_no": 81, "lap": 46, "lap_time": "39:50.628", "position": 7}]
    ours = [{"driver": "Oscar Piastri", "lap": 46, "lap_time_seconds": 0.0, "position": 7}]
    result = fia_check.compare_laps(fia, ours, drivers())
    assert result["checks"][0]["compared"] == 0
    assert result["flags"]["zero_time_laps"] == [
        {"driver": "Oscar Piastri", "lap": 46, "fia": "39:50.628"}
    ]


def test_a_stored_lap_after_the_cars_last_fia_lap_is_flagged():
    fia = [{"car_no": 41, "lap": 58, "lap_time": "1:21.000", "position": 7}]
    ours = [
        {"driver": "Arvid Lindblad", "lap": 58, "lap_time_seconds": 81.0, "position": 7},
        {"driver": "Arvid Lindblad", "lap": 59, "lap_time_seconds": 95.2, "position": 0},
    ]
    beyond = fia_check.compare_laps(fia, ours, drivers())["flags"]["laps_past_fia_last_lap"]
    assert beyond == [{"driver": "Arvid Lindblad", "lap": 59, "fia_last_lap": 58, "position": 0}]


def test_pit_stops_match_on_driver_and_lap_and_report_duration_spread():
    fia = [{"car_no": 81, "lap": 26, "duration": "22.681"}, {"car_no": 27, "lap": 31, "duration": "21.929"}]
    ours = [
        {"driver": "Oscar Piastri", "lap": 26, "duration_seconds": 22.7},
        {"driver": "Nico Hülkenberg", "lap": 30, "duration_seconds": 21.9},
    ]
    check = fia_check.compare_pit_stops(fia, ours, drivers())
    assert check["matched"] == 1
    assert {m["issue"] for m in check["mismatches"]} == {"only in FIA", "only in ours"}
    assert check["max_duration_diff_s"] == 0.019


def test_classification_uses_position_and_points():
    # The final classification, after Piastri's post-race 10 s penalty.
    fia = [{"car_no": 81, "finishing_position": 13, "points": 0.0}]
    stored = [{"driver": "Oscar Piastri", "position": 13, "points": 0.0}]
    assert fia_check.compare_classification(fia, stored, drivers())["matched"] == 1
    provisional = [{"car_no": 81, "finishing_position": 7, "points": 6.0}]
    check = fia_check.compare_classification(provisional, stored, drivers())
    assert check["mismatches"][0]["fia"] == {"position": 7, "points": 6.0}


def test_flags_alone_make_a_report_unclean():
    report = {"checks": [{"mismatches": []}], "flags": {"zero_time_laps": [{"lap": 46}]}}
    assert not fia_check.is_clean(report)
    report["flags"]["zero_time_laps"] = []
    assert fia_check.is_clean(report)


def test_hinted_codes_are_probed_first_and_every_code_still_follows():
    codes = fia_check.codes_to_try("Bahrain Grand Prix in Malaysia")
    assert codes[:2] == ["brn", "bhr"]
    assert set(codes) == set(fia_check.EVENT_CODES)
    assert fia_check.codes_to_try("Miami Grand Prix")[0] == "usa"
    assert fia_check.codes_to_try("Mexico City Grand Prix")[0] == "mex"
