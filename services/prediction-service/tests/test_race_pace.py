"""Race pace from lap times: corrected for fuel and tyres, blind to neutralised laps."""

import numpy as np

from app.services import race_pace
from app.services.ingestion_client import RaceLap, RaceResult

BASE = 90.0
FUEL_PER_LAP = -0.06
WEAR = {"SOFT": 0.08, "HARD": 0.03}
OFFSET = {"SOFT": 0.0, "HARD": 0.6}


def race(pace, laps=50, safety_car=(), seed=0, season=2025, round_number=5):
    """Synthetic race: each driver one stop at half distance, soft then hard."""
    rng = np.random.default_rng(seed)
    out = []
    for driver, delta in pace.items():
        for lap in range(1, laps + 1):
            stint, compound, start = (1, "SOFT", 1) if lap <= laps // 2 else (2, "HARD", laps // 2 + 1)
            time = (BASE + delta + FUEL_PER_LAP * lap + OFFSET[compound]
                    + WEAR[compound] * (lap - start + 1) + rng.normal(0, 0.05))
            if lap in safety_car:
                time += 30.0
            if lap == laps // 2 or lap == laps // 2 + 1:
                time += 20.0  # in-lap and out-lap
            out.append(RaceLap(season=season, round=round_number, driver=driver,
                               lap=lap, lap_time_seconds=time, compound=compound,
                               stint=stint))
    return out


FIELD = {"A": 0.0, "B": 0.3, "C": 0.6, "D": 0.9, "E": 1.2, "F": 1.5}


def test_it_recovers_the_pace_gap_through_fuel_and_tyre_effects():
    gaps = race_pace.race_gaps(race(FIELD))
    assert gaps["A"] == 0.0
    for driver, delta in FIELD.items():
        assert abs(gaps[driver] * BASE - delta) < 0.05


def test_safety_car_laps_do_not_move_the_estimate():
    clean = race_pace.race_gaps(race(FIELD))
    neutralised = race_pace.race_gaps(race(FIELD, safety_car=range(10, 15)))
    for driver in FIELD:
        assert abs(clean[driver] - neutralised[driver]) < 0.0005


def test_a_race_with_too_little_clean_running_says_nothing():
    assert race_pace.race_gaps(race({"A": 0.0, "B": 0.3})) == {}


def test_untimed_and_unlabelled_laps_are_tolerated():
    lap = RaceLap(season=2025, round=1, driver="A", lap=3,
                  lap_time_seconds=None, compound=None, stint=None)
    assert lap.lap_time_seconds == 0.0 and lap.stint == 1 and lap.compound == ""


def test_recent_pace_uses_only_races_before_the_target():
    gaps = {(2025, 1): {"A": 0.01}, (2025, 2): {"A": 0.02}, (2025, 3): {"A": 0.09}}
    assert race_pace.recent_gap(gaps, "A", 2025, 3) == 0.015
    assert race_pace.recent_gap(gaps, "Z", 2025, 3) == race_pace.NEUTRAL_GAP_PCT
    assert race_pace.restrict(gaps, 2025, 3) == {(2025, 1): {"A": 0.01}, (2025, 2): {"A": 0.02}}


def test_the_car_counts_its_better_driver_and_follows_a_rename():
    gaps = {(2025, 1): {"A": 0.010, "B": 0.004}, (2025, 2): {"A": 0.006, "B": 0.012}}
    results = [
        RaceResult(season=2025, round=1, driver="A", team="Racing Point"),
        RaceResult(season=2025, round=1, driver="B", team="Racing Point"),
        RaceResult(season=2025, round=2, driver="A", team="Aston Martin"),
        RaceResult(season=2025, round=2, driver="B", team="Aston Martin"),
    ]
    teams = race_pace.teams_by_race(results)
    assert abs(race_pace.team_recent_gap(gaps, teams, "Aston Martin", 2025, 3) - 0.005) < 1e-12
