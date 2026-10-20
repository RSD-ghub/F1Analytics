"""The lap-by-lap race simulator: mechanics on a synthetic circuit.

Whether it simulates real races well is measured by
``scripts/fit_lap_simulation.py`` on held-out seasons; these pin down that it
behaves like a race.
"""


import numpy as np

from app.models.schemas import DriverFeatures, SimulationStatus
from app.services import lap_simulation
from app.services.lap_simulation import LapRaceEngine, LapSimulationParameters
from app.services.predictor import Predictor

from tests.conftest import make_model
from tests.test_api import FakeStore
from tests.test_neural_simulation import confirmed_client, network_model

PROFILE = {
    "laps": 40, "typical_lap": 90.0, "fuel_per_lap": -0.05, "lap_noise": 0.3,
    "pit_loss": 22.0, "orderliness": 0.78,
    "safety_cars_per_race": 0.0, "vscs_per_race": 0.0, "red_flags_per_race": 0.0,
    "tyres": {c: {"offset": 0.0, "wear": 0.04, "races": 10} for c in ("SOFT", "MEDIUM", "HARD")},
    "strategies": [{"compounds": ["MEDIUM", "HARD"], "fractions": [0.4, 0.6]}],
}


def params(**overrides):
    values = dict(seconds_per_strength=1.0, race_day_sd=0.3, start_shuffle_sd=0.5,
                  overtake_threshold=0.1, overall_orderliness=0.78)
    values.update(overrides)
    return LapSimulationParameters(**values)


def field(n=10, dnf=0.0):
    return [DriverFeatures(driver="D{}".format(i), team="T{}".format(i // 2),
                           dnf_rate=dnf, grid_position=i + 1) for i in range(n)]


def run(strengths, profile=PROFILE, p=None, runs=2000, seed=7, n=None, dnf=0.0, **kw):
    drivers = field(len(strengths) if n is None else n, dnf=dnf)
    return lap_simulation.simulate(drivers, np.asarray(strengths, float), profile,
                                   p or params(), seed=seed, runs=runs, **kw)


def test_every_run_is_a_complete_finishing_order():
    positions, retired = run(np.linspace(1, 0, 10), dnf=0.1)
    assert (np.sort(positions, axis=1) == np.arange(1, 11)).all()
    # Retirements sit behind every finisher.
    finisher_worst = np.where(retired, 0, positions).max(axis=1)
    retired_best = np.where(retired, positions, 99).min(axis=1)
    assert (finisher_worst < retired_best).all()


def test_it_is_reproducible_from_the_seed():
    a = run(np.linspace(1, 0, 10), seed=3)
    b = run(np.linspace(1, 0, 10), seed=3)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_the_faster_car_wins_more():
    positions, _ = run(np.linspace(1, 0, 10))
    wins = (positions == 1).mean(axis=0)
    assert wins[0] > wins[5] > wins[9] or wins[0] > 0.5


def test_where_passing_is_impossible_the_grid_holds():
    """Equal cars, no noise, no stops worth anything: a procession."""
    flat = dict(PROFILE, lap_noise=0.0, pit_loss=0.0,
                strategies=[{"compounds": ["HARD"], "fractions": [1.0]}])
    positions, _ = run(np.zeros(8), profile=flat,
                       p=params(race_day_sd=0.0, start_shuffle_sd=0.0, overtake_threshold=5.0))
    assert (positions == np.arange(1, 9)).all()


def test_a_much_faster_car_from_the_back_gets_through_where_passing_is_easy():
    strengths = np.zeros(8)
    strengths[-1] = 3.0  # three seconds a lap quicker
    easy, _ = run(strengths, p=params(overtake_threshold=0.1))
    hard, _ = run(strengths, p=params(overtake_threshold=5.0))
    assert (easy[:, -1] == 1).mean() > 0.9
    assert (hard[:, -1] == 1).mean() < (easy[:, -1] == 1).mean()


def test_safety_cars_close_up_a_race_and_cost_the_leader():
    strengths = np.array([1.5, 0.0, -0.2, -0.4, -0.6, -0.8])
    calm, _ = run(strengths)
    chaotic, _ = run(strengths, profile=dict(PROFILE, safety_cars_per_race=4.0))
    assert (chaotic[:, 0] == 1).mean() < (calm[:, 0] == 1).mean()


def test_retirements_happen_at_the_cars_rate():
    _, retired = run(np.zeros(10), dnf=0.2, runs=8000)
    assert abs(retired.mean() - 0.2) < 0.02


def engine(enabled):
    return LapRaceEngine(profiles={"overall": PROFILE, "circuits": {}}, params=params(),
                         enabled=enabled, metrics={"test_lap": {"market": 0.5}})


async def test_an_enabled_lap_engine_runs_the_race_simulation():
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model(), lap_engine=engine(True))
    result = await predictor.simulate(2026, 5, runs=500)
    assert result.status is SimulationStatus.READY
    assert result.engine == lap_simulation.LAP_ENGINE_VERSION
    assert abs(sum(d.p_win for d in result.drivers) - 1.0) < 1e-9


async def test_a_disabled_lap_engine_leaves_the_sampler_in_charge():
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model(), lap_engine=engine(False))
    result = await predictor.simulate(2026, 5, runs=500)
    assert result.engine == "plackett-luce"
    assert result.engine_metrics == {}


def test_a_profile_with_no_measured_strategies_falls_back_to_a_one_stop():
    positions, _ = run(np.linspace(1, 0, 6), profile=dict(PROFILE, strategies=[]))
    assert (np.sort(positions, axis=1) == np.arange(1, 7)).all()


def test_an_enabled_pace_network_sets_the_pace():
    """A network that reads the slowest car's features as the fastest pace
    must put that car at the front, whatever the race model thinks."""

    class Backwards:
        def predict(self, x, groups):
            grid = x[:, -2]  # grid_modern: slot number
            return -(grid - grid.mean()) * 2.0  # back of the grid: two percent quicker per slot

    pace = {"network": Backwards(), "scale": 1.0, "race_day_sd": 0.1, "version": "pace-test"}
    engine = LapRaceEngine(profiles={"overall": PROFILE, "circuits": {}}, params=params(),
                           enabled=True, pace=pace)
    drivers = [DriverFeatures(driver="D{}".format(i), team="T{}".format(i // 2),
                              grid_position=i + 1) for i in range(6)]
    model = make_model()
    positions, _ = engine.sample_outcomes(model, drivers, seed=1, runs=500)
    assert (positions[:, -1] == 1).mean() > 0.8
    assert engine.version.endswith("+pace-test")
