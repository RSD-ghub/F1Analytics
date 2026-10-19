"""The neural race model and the race simulation on the confirmed grid.

Mechanics only, on hand-set or synthetic weights — whether the network actually
beats the linear model is a question for ``scripts/train_neural.py`` on the real
corpus, not for a unit test.
"""

import numpy as np
import pytest

from app.models.schemas import DriverFeatures, LockWindow, SimulationStatus
from app.services import race_simulation
from app.services.ingestion_client import GridSlot
from app.services.model import feature_names
from app.services.neural_model import SIMULATION_RUNS, NeuralRaceModel
from app.services.predictor import Predictor
from app.training import neural, plackett_luce
from app.training.plackett_luce import RaceObservation

from tests.conftest import IDENTITY_MEANS, IDENTITY_STDS, TEST_WEIGHTS, make_model
from tests.test_api import FakeClient, FakeStore, client_and_store  # noqa: F401

NAMES = feature_names(with_grid=True)


def linear_only_network(hidden: int = 4) -> neural.NetworkParameters:
    """A network whose hidden layer contributes nothing: the linear model."""
    params = neural.NetworkParameters.initial(len(NAMES), hidden, seed=1)
    params.linear = np.array([TEST_WEIGHTS[name] for name in NAMES])
    return params


def network_model(params=None, **kwargs) -> NeuralRaceModel:
    return NeuralRaceModel(
        params=params or linear_only_network(),
        means=IDENTITY_MEANS, stds=IDENTITY_STDS, noise_scale=2.0, **kwargs,
    )


def field(size: int = 6):
    return [
        DriverFeatures(
            driver="D{}".format(i), team="T{}".format(i // 2),
            avg_finish_recent=float(i + 1), avg_finish_season=float(i + 1),
            team_avg_finish=float(i + 1), grid_position=i + 1, dnf_rate=0.1,
        )
        for i in range(size)
    ]


# ── The network ──────────────────────────────────────────────────────────────


def test_with_a_silent_hidden_layer_the_network_is_the_linear_model():
    """The skip connection is what lets the fit start from a working model."""
    drivers = field()
    assert np.allclose(network_model().strengths(drivers), make_model().strengths(drivers))


def test_the_likelihood_matches_the_linear_one_when_the_network_is_linear():
    rng = np.random.default_rng(0)
    races = [RaceObservation(rng.normal(size=(8, len(NAMES))), [3, 1, 0, 5])
             for _ in range(5)]
    params = linear_only_network()
    linear_ll = plackett_luce.mean_log_likelihood(params.linear, races)
    assert neural.mean_log_likelihood(params, races) == pytest.approx(linear_ll)


def test_the_gradient_is_right():
    """A hand-written backward pass is the thing most likely to be subtly
    wrong, and L-BFGS would still converge — just to the wrong place."""
    rng = np.random.default_rng(3)
    inputs, hidden = 5, 3
    races = [RaceObservation(rng.normal(size=(7, inputs)), [2, 0, 4], weight=0.7)
             for _ in range(4)]
    flat = rng.normal(scale=0.5, size=inputs + hidden * inputs + 2 * hidden)

    _, analytic = neural.negative_log_likelihood(flat, races, inputs, hidden)
    numeric = np.zeros_like(flat)
    step = 1e-6
    for i in range(flat.size):
        up, down = flat.copy(), flat.copy()
        up[i] += step
        down[i] -= step
        numeric[i] = (
            neural.negative_log_likelihood(up, races, inputs, hidden)[0]
            - neural.negative_log_likelihood(down, races, inputs, hidden)[0]
        ) / (2 * step)
    assert np.allclose(analytic, numeric, atol=1e-5)


def test_the_network_learns_an_interaction_a_straight_line_cannot():
    """Strength depends on the product of two features. The linear model can
    only see each on its own; the network should do clearly better on races it
    was not fitted on."""
    rng = np.random.default_rng(7)

    def races(count):
        out = []
        for _ in range(count):
            x = rng.normal(size=(10, 2))
            strength = 2.0 * x[:, 0] * x[:, 1]
            order = list(np.argsort(-(strength + rng.gumbel(size=10))))
            out.append(RaceObservation(x, order))
        return out

    train, test = races(150), races(60)
    linear = plackett_luce.fit(train, ["a", "b"])
    linear_ll = plackett_luce.mean_log_likelihood(
        np.array([linear["a"], linear["b"]]), test)
    params = neural.fit(train, hidden=8, l2_hidden=1e-2)
    assert neural.mean_log_likelihood(params, test) > linear_ll + 1.0


def test_a_pinned_linear_term_is_left_exactly_as_fitted():
    """The network builds on the Plackett-Luce weights; it must not move them."""
    rng = np.random.default_rng(5)
    races = [RaceObservation(rng.normal(size=(8, 3)), list(rng.permutation(8)[:5]))
             for _ in range(30)]
    pinned = np.array([0.4, -0.2, 0.1])
    params = neural.fit(races, hidden=3, l2_hidden=1.0, fixed_linear=pinned)
    assert np.array_equal(params.linear, pinned)


def test_with_nothing_to_learn_the_network_is_the_fitted_model():
    """Orders drawn from the linear model itself leave a hidden layer nothing
    real to find, so validation should pick the pinned model alone."""
    rng = np.random.default_rng(8)
    pinned = np.array([1.0, -0.5])

    def races(count):
        out = []
        for _ in range(count):
            x = rng.normal(size=(10, 2))
            order = list(np.argsort(-(x @ pinned + rng.gumbel(size=10))))
            out.append(RaceObservation(x, order))
        return out

    params, chosen, sweep = neural.select(
        races(40), races(40), grid=((8, 0.01),), fixed_linear=pinned)
    assert sweep[0]["hidden"] == 0
    assert chosen["hidden"] == 0
    assert np.array_equal(params.linear, pinned)
    assert not params.output.any()


def test_the_fit_cannot_run_away():
    """Bounded weights: the first real training run overflowed the hidden layer
    on its lightly penalised candidates."""
    rng = np.random.default_rng(9)
    races = [RaceObservation(rng.normal(size=(6, 2)) * 50, [0, 1, 2]) for _ in range(20)]
    params = neural.fit(races, hidden=4, l2_hidden=0.0)
    assert np.abs(params.flatten()).max() <= neural.WEIGHT_BOUND


def test_parameters_survive_a_round_trip():
    params = neural.NetworkParameters.initial(len(NAMES), 5, seed=2)
    again = neural.NetworkParameters.from_dict(params.to_dict())
    assert np.array_equal(again.flatten(), params.flatten())


def test_an_artifact_for_a_different_feature_set_is_refused():
    with pytest.raises(ValueError):
        network_model(params=neural.NetworkParameters.initial(3, 2))


def test_a_driver_missing_from_the_grid_is_not_read_as_pole():
    drivers = field(4)
    drivers[3] = drivers[3].model_copy(update={"grid_position": 0})
    strengths = network_model().strengths(drivers)
    assert strengths[3] == min(strengths)


# ── The simulation ───────────────────────────────────────────────────────────


def test_it_runs_ten_thousand_races_by_default():
    assert SIMULATION_RUNS == 10000
    assert network_model().runs == 10000


def test_every_run_ends_somewhere():
    """Each driver's positions and retirements account for every simulated race."""
    out = race_simulation.simulate(network_model(), field(), seed=11, runs=5000)
    for driver in out:
        assert sum(driver.position_probabilities) + driver.p_dnf == pytest.approx(1.0)
        assert len(driver.position_probabilities) == 6


def test_one_winner_per_race():
    out = race_simulation.simulate(network_model(), field(), seed=11, runs=5000)
    assert sum(d.p_win for d in out) == pytest.approx(1.0, abs=1e-3)


def test_markets_are_nested():
    for d in race_simulation.simulate(network_model(), field(), seed=5, runs=2000):
        assert d.p_win <= d.p_podium <= d.p_points


def test_pole_is_favoured():
    out = race_simulation.simulate(network_model(), field(), seed=5, runs=5000)
    assert out[0].driver == "D0"


def test_it_is_reproducible_from_the_seed():
    a = race_simulation.simulate(network_model(), field(), seed=9, runs=1000)
    b = race_simulation.simulate(network_model(), field(), seed=9, runs=1000)
    assert a == b


def test_splitting_out_retirements_leaves_locked_forecasts_untouched():
    """``sample_orders`` now goes through ``sample_outcomes``; a seed must still
    give the same orders, or every locked forecast stops being reproducible."""
    model = make_model()
    drivers = field()
    positions, _ = model.sample_outcomes(drivers, seed=4, runs=500)
    assert np.array_equal(model.sample_orders(drivers, seed=4, runs=500), positions)


def test_the_linear_models_numbers_ride_along():
    baseline = make_model().predict(field(), seed=1)
    out = race_simulation.simulate(
        network_model(), field(), seed=1, runs=500, baseline=baseline)
    assert all(d.baseline is not None and d.baseline.driver == d.driver for d in out)


# ── Wiring into the predictor ────────────────────────────────────────────────


def confirmed_client():
    client = FakeClient()

    async def grid(season, round_number):
        return [
            GridSlot(season=season, round=round_number, driver=d, team=t,
                     position=i + 1, grid_position=i + 1, grid_source="official_final")
            for i, (d, t) in enumerate(
                [("Max Verstappen", "Red Bull"), ("Lando Norris", "McLaren"),
                 ("Charles Leclerc", "Ferrari"), ("George Russell", "Mercedes")]
            )
        ]

    client.grid = grid
    return client


async def test_without_a_trained_network_it_simulates_on_the_fitted_model():
    """The simulation must always work: no network means the Plackett-Luce
    model runs the ten thousand races itself, with nothing to compare against."""
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model())
    result = await predictor.simulate(2026, 5)
    assert result.status is SimulationStatus.READY
    assert result.runs == 10000
    assert result.model_version == make_model().version
    assert result.baseline_model_version == ""
    assert result.drivers and all(d.baseline is None for d in result.drivers)


async def test_it_waits_for_the_confirmed_grid():
    """Qualifying order is not the grid; a penalised driver would start from
    the wrong slot in every one of the ten thousand races."""
    predictor = Predictor(client=FakeClient(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model())
    result = await predictor.simulate(2026, 5)
    assert result.status is SimulationStatus.AWAITING_CONFIRMED_GRID
    assert "not confirmed" in result.detail


async def test_on_the_confirmed_grid_it_simulates_with_the_network():
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model())
    result = await predictor.simulate(2026, 5)
    assert result.status is SimulationStatus.READY
    assert result.runs == 10000
    assert result.model_version == "race-neural-v1"
    assert result.baseline_model_version == make_model().version
    assert {d.driver for d in result.drivers} >= {"Max Verstappen", "Lando Norris"}


async def test_an_unpromoted_network_does_not_place_the_final_grid_forecast():
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model(promoted=False))
    prediction, _ = await predictor.build(2026, 5, LockWindow.FINAL_GRID)
    assert prediction.model_version == make_model().version


async def test_a_promoted_network_places_only_the_final_grid_forecast():
    predictor = Predictor(client=confirmed_client(), store=FakeStore(), model=make_model(),
                          final_grid_model=network_model(promoted=True))
    final, _ = await predictor.build(2026, 5, LockWindow.FINAL_GRID)
    post, _ = await predictor.build(2026, 5, LockWindow.POST_QUALI)
    assert final.model_version == "race-neural-v1"
    assert post.model_version == make_model().version


# ── The endpoint ─────────────────────────────────────────────────────────────


def test_the_endpoint_reports_a_missing_confirmed_grid_as_a_state(client_and_store):
    """200 with a status, not a 409: the page shows "waiting for the grid"
    rather than a broken panel, and core-api can cache it like any answer."""
    from app.main import app
    from app import dependencies

    client, store = client_and_store
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor(
        client=FakeClient(), store=store, model=make_model(),
        final_grid_model=network_model(),
    )
    response = client.get("/predictions/2026/5/simulation")
    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_confirmed_grid"


def test_the_endpoint_returns_the_simulation(client_and_store):
    from app.main import app
    from app import dependencies

    client, store = client_and_store
    app.dependency_overrides[dependencies.get_predictor] = lambda: Predictor(
        client=confirmed_client(), store=store, model=make_model(),
        final_grid_model=network_model(),
    )
    body = client.get("/predictions/2026/5/simulation", params={"runs": 1000}).json()
    assert body["status"] == "ready"
    assert body["runs"] == 1000
    assert body["drivers"][0]["position_probabilities"]
