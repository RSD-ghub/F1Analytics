"""Strategy profile tests: the measured inputs to Bernie's race plans.

Built on a hand-made corpus whose answers are known — a tyre that wears a set
amount a lap, stops that cost a set amount — because these are the numbers
that look plausible while being quietly wrong. Fuel burn hiding tyre wear is
the main way they go wrong, and it gets its own test.
"""

import pytest

from app.services.strategy_profile import FUEL_SECONDS_PER_LAP, summarise_strategy

BASE = 90.0
PIT_LOSS = 21.0


def _race(season, compounds=(("MEDIUM", 1, 25, 0.07), ("HARD", 26, 50, 0.04)),
          drivers=("A", "B", "C")):
    """A 50-lap race: each driver runs the given stints with exact wear.

    Lap time = base + compound wear x tyre age - fuel burn x lap, with the
    stop's cost split across the in-lap and out-lap.
    """
    laps, stops, results = [], [], []
    for position, driver in enumerate(drivers, start=1):
        for stint, (compound, first, last, wear) in enumerate(compounds, start=1):
            for lap in range(first, last + 1):
                time = BASE + wear * (lap - first) - FUEL_SECONDS_PER_LAP * lap
                if lap == last and stint < len(compounds):
                    time += PIT_LOSS / 2
                if lap == first and stint > 1:
                    time += PIT_LOSS / 2
                laps.append({"season": season, "round": 1, "driver": driver,
                             "lap": lap, "lap_time_seconds": time,
                             "compound": compound, "stint": stint})
            if stint < len(compounds):
                stops.append({"season": season, "round": 1, "driver": driver,
                              "stop": stint, "lap": last})
        results.append({"season": season, "round": 1, "driver": driver,
                        "position": position, "classified_position": str(position)})
    return results, laps, stops


def _profile(seasons=(2023, 2024, 2025), **kwargs):
    results, laps, stops = [], [], []
    for season in seasons:
        r, l, s = _race(season, **kwargs)
        results += r
        laps += l
        stops += s
    return results, laps, stops


def test_race_distance_and_pit_loss_are_measured():
    results, laps, stops = _profile()
    profile = summarise_strategy("Test", results, laps, stops, [], [])
    assert profile["race_laps"] == 50
    assert profile["pit_loss_seconds"] == pytest.approx(PIT_LOSS, abs=1.5)
    assert profile["pit_loss_sample"] == 9


def test_wear_is_corrected_for_fuel_burn():
    """Raw lap times fall by the fuel effect each lap; wear must not."""
    results, laps, stops = _profile()
    compounds = summarise_strategy("Test", results, laps, stops, [], [])["compounds"]
    assert compounds["MEDIUM"]["degradation_seconds_per_lap"] == pytest.approx(0.07, abs=0.005)
    assert compounds["HARD"]["degradation_seconds_per_lap"] == pytest.approx(0.04, abs=0.005)


def test_stint_lengths_and_stop_counts():
    results, laps, stops = _profile()
    profile = summarise_strategy("Test", results, laps, stops, [], [])
    assert profile["compounds"]["MEDIUM"]["longest_stint_laps"] == 25
    assert profile["winner_stop_counts"] == {"1": 3}
    assert profile["top_ten_stop_counts"] == {"1": 9}


def test_a_compound_pace_offset_is_measured_against_the_medium():
    """A soft run in the same race, 0.6s quicker when new."""
    results, laps, stops = [], [], []
    for season in (2024, 2025):
        r, l, s = _race(season)
        r2, l2, s2 = _race(
            season,
            compounds=(("SOFT", 1, 20, 0.1), ("MEDIUM", 21, 50, 0.07)),
            drivers=("D", "E"),
        )
        for lap in l2:
            if lap["compound"] == "SOFT":
                lap["lap_time_seconds"] -= 0.6
        results += r + r2
        laps += l + l2
        stops += s + s2
    soft = summarise_strategy("Test", results, laps, stops, [], [])["compounds"]["SOFT"]
    assert soft["pace_offset_seconds"] == pytest.approx(-0.6, abs=0.1)


def test_virtual_safety_cars_are_not_counted_as_safety_cars():
    control = [
        {"season": 2024, "round": 1, "message": "VIRTUAL SAFETY CAR DEPLOYED"},
        {"season": 2025, "round": 1, "message": "SAFETY CAR DEPLOYED"},
        {"season": 2023, "round": 1, "message": "GREEN LIGHT - PIT EXIT OPEN"},
    ]
    results, laps, stops = _profile()
    profile = summarise_strategy("Test", results, laps, stops, control, [])
    assert profile["race_control_races"] == 3
    assert profile["safety_car_races"] == 1
    assert profile["virtual_safety_car_races"] == 1


def test_wet_compounds_are_left_out_of_the_tyre_fits():
    results, laps, stops = _profile(
        compounds=(("INTERMEDIATE", 1, 25, 0.07), ("HARD", 26, 50, 0.04))
    )
    compounds = summarise_strategy("Test", results, laps, stops, [], [])["compounds"]
    assert "INTERMEDIATE" not in compounds
    assert "HARD" in compounds


def test_nothing_held_means_nothing_claimed():
    """No laps, no figures — the simulator, not this, decides what to assume."""
    profile = summarise_strategy("Test", [], [], [], [], [])
    assert profile["races_in_sample"] == 0
    for key in ("race_laps", "pit_loss_seconds", "compounds", "safety_car_races"):
        assert key not in profile
