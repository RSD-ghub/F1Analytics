"""The diff half of scripts/repair_laps_from_fia.py. No Mongo, no network.

Rows are cut from Mexico City 2026: fia-doc's lap table against our laps.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import repair_laps_from_fia as repair  # noqa: E402


def lap(driver, number, seconds, position=7, stint=3, _id=None):
    return {"_id": _id or "{}-{}".format(driver, number), "driver": driver, "lap": number,
            "lap_time_seconds": seconds, "position": position, "stint": stint}


FIA = [
    {"car_no": 41, "lap": 44, "lap_time": "1:21.420", "position": 7},
    {"car_no": 41, "lap": 45, "lap_time": "1:51.518", "position": 7},
    {"car_no": 41, "lap": 46, "lap_time": "40:02.202", "position": 7},
    {"car_no": 41, "lap": 58, "lap_time": "1:21.158", "position": 7},
    {"car_no": 81, "lap": 44, "lap_time": "1:20.559", "position": 8},
    {"car_no": 81, "lap": 45, "lap_time": "1:33.284", "position": 8},
]
OURS = [
    lap("Arvid Lindblad", 44, 81.42), lap("Arvid Lindblad", 45, 111.518),
    lap("Arvid Lindblad", 46, 0.0), lap("Arvid Lindblad", 58, 81.158),
    lap("Arvid Lindblad", 59, 0.0, position=0),
    lap("Oscar Piastri", 44, 80.559, 8, 2), lap("Oscar Piastri", 45, 93.284, 8, 2),
]
STINTS = [{"_id": "s-lin-3", "driver": "Arvid Lindblad", "stint": 3, "laps": 14},
          {"_id": "s-pia-2", "driver": "Oscar Piastri", "stint": 2, "laps": 20}]


def test_cars_are_matched_to_drivers_by_their_lap_times():
    assert repair.match_cars(FIA, OURS) == {41: "Arvid Lindblad", 81: "Oscar Piastri"}


def test_a_car_whose_times_agree_with_nobody_stays_unmatched():
    stranger = [{"car_no": 99, "lap": 44, "lap_time": "1:30.000", "position": 1}]
    assert repair.match_cars(stranger, OURS) == {}


def test_an_untimed_lap_takes_the_fia_time_and_keeps_its_position():
    plan = repair.plan_repairs(FIA, OURS, STINTS, repair.match_cars(FIA, OURS))
    assert [(f["driver"], f["lap"], f["set"]) for f in plan["fills"]] == [
        ("Arvid Lindblad", 46, {"lap_time_seconds": 2402.202})
    ]


def test_a_zero_position_is_filled_alongside_the_time():
    ours = [lap("Arvid Lindblad", 44, 81.42), lap("Arvid Lindblad", 46, 0.0, position=0)]
    plan = repair.plan_repairs(FIA, ours, [], {41: "Arvid Lindblad"})
    assert plan["fills"][0]["set"] == {"lap_time_seconds": 2402.202, "position": 7}


def test_timed_laps_are_left_alone():
    plan = repair.plan_repairs(FIA, OURS, STINTS, {41: "Arvid Lindblad", 81: "Oscar Piastri"})
    assert {(f["driver"], f["lap"]) for f in plan["fills"]} == {("Arvid Lindblad", 46)}


def test_the_lap_after_a_cars_last_fia_lap_is_an_extra_and_its_stint_shrinks():
    plan = repair.plan_repairs(FIA, OURS, STINTS, {41: "Arvid Lindblad", 81: "Oscar Piastri"})
    assert [(e["driver"], e["lap"], e["fia_last_lap"]) for e in plan["extras"]] == [
        ("Arvid Lindblad", 59, 58)
    ]
    assert plan["stints"] == [{"_id": "s-lin-3", "driver": "Arvid Lindblad", "stint": 3,
                               "laps": 14, "new_laps": 13, "delete": False}]


def test_a_stint_left_with_no_laps_is_deleted():
    ours = OURS + [lap("Arvid Lindblad", 60, 0.0, position=0, stint=4)]
    stints = STINTS + [{"_id": "s-lin-4", "driver": "Arvid Lindblad", "stint": 4, "laps": 1}]
    plan = repair.plan_repairs(FIA, ours, stints, {41: "Arvid Lindblad"})
    assert {s["_id"]: s["delete"] for s in plan["stints"]} == {"s-lin-3": False, "s-lin-4": True}


def test_a_driver_the_fia_does_not_list_gets_no_extras():
    plan = repair.plan_repairs(FIA, OURS, STINTS, {41: "Arvid Lindblad"})
    assert all(e["driver"] == "Arvid Lindblad" for e in plan["extras"])
