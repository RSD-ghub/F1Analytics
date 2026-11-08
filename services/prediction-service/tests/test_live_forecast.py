"""Live forecasts: the simulator restarted from a real race, and the replay scoring.

Whether live forecasts beat the pre-race one and "the order holds" on real
races is measured by ``scripts/backtest_live.py``; these pin down the mechanics
on synthetic races.
"""

import numpy as np
import pytest

from app.models.schemas import DriverFeatures
from app.services import lap_simulation, live_forecast
from app.services.ingestion_client import PitStop, RaceControlMessage, RaceLap, RaceResult
from app.services.lap_simulation import DRY, LiveStart
from app.services.live_state import neutralisation, race_state
from app.training import live_backtest

from tests.test_lap_simulation import PROFILE, params

N = 6
DRIVERS = ["D{}".format(i) for i in range(N)]


def field(dnf=0.0):
    return [DriverFeatures(driver=d, team="T{}".format(i // 2), dnf_rate=dnf,
                           grid_position=i + 1) for i, d in enumerate(DRIVERS)]


def start(**overrides):
    values = dict(
        lap=20, total_laps=40, gaps=np.arange(N) * 2.0, retired=np.zeros(N, bool),
        retired_lap=np.full(N, 41), compound=np.full(N, DRY.index("MEDIUM")),
        tyre_age=np.full(N, 20.0), stops=np.zeros(N, int),
        used=np.tile([False, True, False], (N, 1)), pace=np.zeros(N),
        pace_sd=np.full(N, 0.1))
    values.update(overrides)
    return LiveStart(**values)


def run_from(s, runs=1000, seed=3, dnf=0.0, profile=PROFILE):
    return lap_simulation.simulate(field(dnf), np.zeros(N), profile, params(),
                                   seed=seed, runs=runs, start=s)


# --- the simulator, restarted -------------------------------------------------

def test_a_big_lead_with_two_laps_left_wins():
    s = start(lap=38, gaps=np.array([0.0, 40, 42, 44, 46, 48]), tyre_age=np.full(N, 5.0),
              stops=np.ones(N, int), used=np.tile([False, True, True], (N, 1)))
    positions, _ = run_from(s)
    assert (positions[:, 0] == 1).mean() > 0.99


def test_retired_cars_stay_out_and_behind_every_finisher():
    retired = np.array([True, False, False, False, False, False])
    s = start(retired=retired, retired_lap=np.array([12, 41, 41, 41, 41, 41]))
    positions, out = run_from(s)
    assert out[:, 0].all()
    assert (positions[:, 0] == N).all()


def test_pace_from_the_race_decides_who_comes_through():
    # Car 5 is 10 s behind but 1.5 s a lap quicker with 20 laps to go.
    gaps = np.array([0.0, 2, 4, 6, 8, 10])
    quick = np.array([0.3, 0.3, 0.3, 0.3, 0.3, -1.2])
    positions, _ = run_from(start(pace=quick, gaps=gaps))
    baseline, _ = run_from(start(gaps=gaps))
    assert positions[:, 5].mean() < baseline[:, 5].mean() - 2


def test_every_plan_meets_the_two_compound_rule_in_the_dry():
    s = start(lap=10)
    rng = np.random.default_rng(0)
    compounds, ends = lap_simulation._remaining_strategies(PROFILE, rng, 500, s)
    for i in range(N):
        planned = compounds[:, i, :]
        for run in range(500):
            used = set(np.flatnonzero(s.used[i])) | set(planned[run][planned[run] >= 0])
            assert len(used) >= 2
        # Stops still to come are after now and before the flag.
        stops = ends[:, i, :][ends[:, i, :] <= s.total_laps]
        assert (stops > s.lap).all() and (stops <= s.total_laps - 1).all()


def test_no_extra_stop_is_forced_in_the_wet():
    s = start(lap=10, wet=True, compound=np.full(N, -1), used=np.zeros((N, 3), bool),
              stops=np.full(N, 5))
    compounds, ends = lap_simulation._remaining_strategies(
        PROFILE, np.random.default_rng(0), 50, s)
    assert (ends[:, :, 0] == s.total_laps + 1).all()


def test_behind_a_safety_car_to_the_flag_nobody_passes():
    s = start(lap=36, gaps=np.array([0.0, 10, 20, 30, 40, 50]), safety_car_laps=4,
              stops=np.ones(N, int), used=np.tile([False, True, True], (N, 1)),
              tyre_age=np.full(N, 5.0))
    positions, _ = run_from(s)
    assert (positions == np.arange(1, N + 1)).all()


def test_the_grid_path_is_unchanged_by_the_new_argument():
    a = lap_simulation.simulate(field(), np.linspace(1, 0, N), PROFILE, params(),
                                seed=5, runs=200)
    b = lap_simulation.simulate(field(), np.linspace(1, 0, N), PROFILE, params(),
                                seed=5, runs=200, start=None)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


# --- the race state, from timing rows ----------------------------------------

def scripted_race(laps=30, pace=None, missing=(), retire=None, lapped=None):
    """Lap rows for a dry race: car i is pace[i] s a lap off 90 s, starts 2i s
    back, and makes one 22 s stop at the end of lap 15."""
    pace = np.zeros(N) if pace is None else np.asarray(pace)
    rows = []
    for i, driver in enumerate(DRIVERS):
        last = laps
        if retire and driver == retire[0]:
            last = retire[1]
        if lapped and driver == lapped:
            last = laps - 1
        for lap in range(1, last + 1):
            stint = 1 if lap <= 15 else 2
            seconds = 90.0 + pace[i] + (2.0 * i if lap == 1 else 0.0)
            if lap in (15, 16):
                seconds += 11.0  # the stop, split over in-lap and out-lap
            if (driver, lap) in missing:
                seconds = 0.0
            rows.append(RaceLap(season=2025, round=1, driver=driver, lap=lap,
                                lap_time_seconds=seconds,
                                compound="MEDIUM" if stint == 1 else "HARD", stint=stint))
    return rows


def results(retired=(), lapped=()):
    out = []
    for i, d in enumerate(DRIVERS):
        status, classified = "Finished", str(i + 1)
        if d in retired:
            status, classified = "Engine", "R"
        elif d in lapped:
            status = "+1 Lap"
        out.append(RaceResult(season=2025, round=1, driver=d, position=i + 1,
                              classified_position=classified, status=status))
    return out


def state_at(lap, laps, res=None, messages=(), stops=()):
    return race_state(DRIVERS, laps, list(stops), list(messages),
                      res if res is not None else results(), upto=lap, total_laps=30,
                      profile=PROFILE)


def test_gaps_and_order_are_rebuilt_from_lap_times():
    state = state_at(10, scripted_race(pace=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5]))
    assert state.running_order() == DRIVERS
    # Car 5: 10 s back after the start, then 0.5 s a lap over 10 laps.
    assert state.gaps[5] == pytest.approx(10.0 + 0.5 * 10)
    assert not state.retired.any()


def test_a_missing_lap_time_does_not_move_a_car():
    laps = scripted_race(pace=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                         missing={("D0", 5), ("D3", 6), ("D3", 7)})
    assert state_at(10, laps).running_order() == DRIVERS


def test_tyres_stops_and_compounds_used():
    state = state_at(20, scripted_race())
    assert (state.stops == 1).all()
    assert state.tyre_age[0] == 5
    assert state.compound[0] == "HARD"
    assert state.used[0].tolist() == [False, True, True]


def test_a_stopped_car_is_retired_and_a_lapped_one_is_not():
    laps = scripted_race(retire=("D2", 12), lapped="D4")
    res = results(retired=("D2",), lapped=("D4",))
    assert not state_at(10, laps, res).retired.any()  # nothing from the future
    late = state_at(30, laps, res)
    assert late.retired.tolist() == [False, False, True, False, False, False]
    assert late.retired_lap[2] == 12
    assert late.gaps[4] > 80  # a lap down
    assert late.running_order()[-1] == "D2"


def test_pace_this_race_is_measured_against_the_field():
    state = state_at(12, scripted_race(pace=[0.0, 0.0, 0.0, 0.0, 0.0, -0.8]))
    assert state.observed_laps[5] == 11  # laps 2-12, all clean
    assert state.observed_pace[5] == pytest.approx(-0.8, abs=0.05)
    assert state.observed_pace[0] == pytest.approx(0.0, abs=0.05)


def test_in_and_out_laps_and_neutralised_laps_do_not_count_as_pace():
    messages = [
        RaceControlMessage(season=2025, round=1, category="SafetyCar",
                           message="SAFETY CAR DEPLOYED", lap=5),
        RaceControlMessage(season=2025, round=1, category="SafetyCar",
                           message="SAFETY CAR IN THIS LAP", lap=7),
    ]
    state = state_at(20, scripted_race(), messages=messages)
    # Laps 2-20 = 19; minus laps 5-7 under the safety car, the in-lap and out-lap.
    assert state.observed_laps[0] == 19 - 3 - 2


def test_a_safety_car_out_at_the_checkpoint_carries_on():
    messages = [RaceControlMessage(season=2025, round=1, category="SafetyCar",
                                   message="SAFETY CAR DEPLOYED", lap=9)]
    neutral, sc_left, vsc_left = neutralisation(messages, 10)
    assert sc_left == lap_simulation.SAFETY_CAR_LAPS - 1 and vsc_left == 0
    assert {9, 10} <= neutral
    assert neutralisation(messages, 8)[1] == 0  # not yet deployed


def test_stops_come_from_the_pit_lane_without_stint_numbers():
    laps = [lap.model_copy(update={"stint": 0}) for lap in scripted_race()]
    stops = [PitStop(season=2025, round=1, driver=d, stop=1, lap=15) for d in DRIVERS]
    state = state_at(20, laps, stops=stops)
    assert (state.stops == 1).all() and state.tyre_age[0] == 5


# --- pace updated from the race ----------------------------------------------

def test_with_no_laps_the_prior_stands():
    state = state_at(1, scripted_race())
    prior = np.linspace(-0.5, 0.5, N)
    pace, spread = live_forecast.posterior_pace(prior, state, 0.3, prior_laps=10)
    assert np.allclose(pace, prior)
    assert np.allclose(spread, 0.3)


def test_many_laps_outweigh_the_prior_and_shrink_the_spread():
    state = state_at(29, scripted_race(pace=[0.0, 0.0, 0.0, 0.0, 0.0, -0.8]))
    pace, spread = live_forecast.posterior_pace(np.zeros(N), state, 0.3, prior_laps=3)
    assert pace[5] - pace[:5].mean() < -0.6
    assert (spread < 0.15).all()


# --- the whole forecast -------------------------------------------------------

def test_the_pre_race_favourite_who_retired_cannot_win():
    laps = scripted_race(retire=("D0", 8))
    state = state_at(10, laps, results(retired=("D0",)))
    strengths = np.array([3.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    pre = live_forecast.forecast(field(), strengths, PROFILE, params(), None, seed=1, runs=500)
    live = live_forecast.forecast(field(), strengths, PROFILE, params(), state, seed=1, runs=500)
    assert ((pre[0][:, 0] == 1) & ~pre[1][:, 0]).mean() > 0.5
    assert ((live[0][:, 0] == 1) & ~live[1][:, 0]).mean() == 0.0
    rows = live_forecast.summarise(field(), *live)
    assert rows[-1]["driver"] == "D0" and rows[-1]["retire"] == 1.0


def test_drivers_must_match_the_state():
    state = state_at(10, scripted_race())
    with pytest.raises(ValueError):
        live_forecast.forecast(list(reversed(field())), np.zeros(N), PROFILE, params(),
                               state, seed=1, runs=10)


# --- replay scoring -------------------------------------------------------------

def test_checkpoints_never_reach_the_flag():
    assert live_backtest.checkpoint_laps(50) == [0, 5, 12, 25, 38, 45]
    assert live_backtest.checkpoint_laps(3)[-1] <= 2


def test_order_holds_is_the_running_order_with_certainty():
    state = state_at(20, scripted_race())
    positions, retired = live_backtest.order_holds(state)
    assert positions.tolist() == [[1, 2, 3, 4, 5, 6]]
    assert live_backtest.score(positions, retired, [1, 2, 3, 4, 5, 6])["winner_hit"] == 1.0


def test_score_floors_a_confident_miss():
    positions = np.array([[1, 2, 3]])
    scored = live_backtest.score(positions, np.zeros((1, 3), bool), [2, 1, 3])
    assert scored["winner_log_loss"] == pytest.approx(-np.log(live_backtest.PROBABILITY_FLOOR))
    assert scored["winner_hit"] == 0.0
    assert scored["position_error"] == pytest.approx(2 / 3)


def test_paired_difference_over_races():
    rows = []
    for race in range(20):
        rows.append(dict(race=race, method="live", checkpoint=0.5, podium_brier=0.4))
        rows.append(dict(race=race, method="pre_race", checkpoint=0.5, podium_brier=0.6))
    mean, (low, high), count = live_backtest.paired_difference(
        rows, "live", "pre_race", 0.5, "podium_brier")
    assert count == 20 and mean == pytest.approx(-0.2) and high < 0


def test_dropping_laps_blanks_times_only():
    laps = scripted_race()
    dropped = live_backtest.drop_laps(laps, 0.5, np.random.default_rng(0))
    assert len(dropped) == len(laps)
    blank = sum(1 for lap in dropped if lap.lap_time_seconds == 0)
    assert 0.4 * len(laps) < blank < 0.6 * len(laps)


def test_a_missing_in_lap_still_costs_the_stop():
    # D1 runs 0.1 s a lap behind D0. Lose D1's in-lap time: it must not jump
    # D0, which is still being charged its own stop.
    laps = scripted_race(pace=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5], missing={("D1", 15)})
    state = state_at(20, laps)
    assert state.running_order() == DRIVERS
    assert state.gaps[1] > state.gaps[0]


# --- data quirks met in real races --------------------------------------------

def test_a_safety_car_with_no_in_this_lap_ends_at_drs_enabled():
    # Spa 2025: a red flag on lap 1, a rolling start, and no "IN THIS LAP".
    messages = [RaceControlMessage(season=2025, round=1, category="Flag", message="RED FLAG", lap=1),
                RaceControlMessage(season=2025, round=1, category="Other",
                                   message="DRS ENABLED", lap=12)]
    neutral, sc_left, _ = neutralisation(messages, upto=20)
    assert sc_left == 0
    assert neutral == set(range(1, 12))


def test_early_stint_changes_on_the_same_tyre_are_not_stops():
    # Australia 2025: every car has stints 1-3 on laps 1-4, all intermediates.
    laps = []
    for i, driver in enumerate(DRIVERS):
        for lap in range(1, 21):
            stint = {1: 1, 2: 1, 3: 2, 4: 3}.get(lap, 4 if lap <= 12 else 5)
            compound = "INTERMEDIATE" if lap <= 12 else "HARD"
            laps.append(RaceLap(season=2025, round=1, driver=driver, lap=lap,
                                lap_time_seconds=90.0 + i * 0.1, compound=compound,
                                stint=stint))
    state = state_at(20, laps)
    assert (state.stops == 1).all()
    assert (state.tyre_age == 8).all()
