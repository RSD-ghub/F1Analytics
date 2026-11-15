"""The live network: a residual on the simulator's chances, learned from replays."""

import numpy as np
import pytest

from app.training import live_backtest
from app.training.live_network import (
    FEATURES, LiveNetwork, Sample, choose_l2, features, held_out_loss)

from tests.test_lap_simulation import PROFILE
from tests.test_live_forecast import DRIVERS, scripted_race, state_at

N = 6


def synthetic(count=120, seed=0, bias=2.0):
    """Races where the simulator is too unsure of the leader: the leader (x0
    high) wins far more often than its chance says."""
    rng = np.random.default_rng(seed)
    samples = []
    for j in range(count):
        x = rng.normal(size=(N, len(FEATURES)))
        leader = int(np.argmax(x[:, 0]))
        sim_win = np.full(N, 1.0 / N)
        sim_podium = np.full(N, 0.5)
        true = np.exp(bias * (np.arange(N) == leader))
        winner = int(rng.choice(N, p=true / true.sum()))
        podium = np.zeros(N)
        podium[[winner, (winner + 1) % N, (winner + 2) % N]] = 1.0
        samples.append(Sample(x, sim_win, sim_podium, np.ones(N, bool), winner, podium,
                              group=j % 2))
    return samples


def test_zero_weights_return_the_simulator():
    samples = synthetic(10)
    net = LiveNetwork().fit(samples)
    net.weights = np.zeros_like(net.weights)
    s = samples[0]
    win, podium = net.predict(s.x, np.array([0.5, 0.2, 0.1, 0.1, 0.05, 0.05]), s.sim_podium,
                              s.running)
    assert np.allclose(win, [0.5, 0.2, 0.1, 0.1, 0.05, 0.05])
    assert np.allclose(podium, 0.5)


def test_it_learns_a_consistent_error_and_beats_the_simulator_held_out():
    train, test = synthetic(200, seed=1), synthetic(200, seed=2)
    net = LiveNetwork(l2=10.0).fit(train)
    assert held_out_loss(net, test) < held_out_loss(None, test) - 0.1


def test_with_nothing_to_learn_it_stays_near_the_simulator():
    train, test = synthetic(200, seed=1, bias=0.0), synthetic(200, seed=2, bias=0.0)
    net = LiveNetwork(l2=100.0).fit(train)
    assert held_out_loss(net, test) < held_out_loss(None, test) + 0.05


def test_win_chances_sum_to_one_over_running_cars_and_retired_cars_get_none():
    net = LiveNetwork().fit(synthetic(40))
    running = np.array([True, True, False, True, True, True])
    win, podium = net.predict(np.random.default_rng(0).normal(size=(N, len(FEATURES))),
                              np.full(N, 1 / N), np.full(N, 0.5), running)
    assert win.sum() == pytest.approx(1.0)
    assert win[2] == 0 and podium[2] == 0


def test_gradient_matches_finite_differences():
    samples = synthetic(8)
    net = LiveNetwork(hidden=3, l2=2.0)
    net.fit(samples)  # sets scaling and sizes
    x = net._scale(np.vstack([s.x for s in samples]))
    rng = np.random.default_rng(0)
    flat = rng.normal(0, 0.1, net.weights.size)

    # Rebuild the objective exactly as fit() does, through a tiny refit hook.
    captured = {}
    from scipy import optimize
    real = optimize.minimize

    def spy(fun, x0, **kw):
        captured["fun"] = fun
        return real(fun, x0, **kw)

    import app.training.live_network as module
    module.minimize = spy
    try:
        LiveNetwork(hidden=3, l2=2.0).fit(samples)
    finally:
        module.minimize = real
    fun = captured["fun"]
    _, grad = fun(flat)
    eps = 1e-6
    for i in rng.choice(flat.size, 12, replace=False):
        up, down = flat.copy(), flat.copy()
        up[i] += eps
        down[i] -= eps
        numeric = (fun(up)[0] - fun(down)[0]) / (2 * eps)
        assert grad[i] == pytest.approx(numeric, rel=1e-3, abs=1e-6)
    assert x.shape[1] == len(FEATURES)


def test_round_trip():
    net = LiveNetwork().fit(synthetic(20))
    again = LiveNetwork.from_dict(net.to_dict())
    s = synthetic(1, seed=9)[0]
    a = net.predict(s.x, s.sim_win, s.sim_podium, s.running)
    b = again.predict(s.x, s.sim_win, s.sim_podium, s.running)
    assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_choose_l2_holds_out_each_season():
    best, scores, baseline = choose_l2(synthetic(80, seed=3), candidates=(10.0, 100.0))
    assert best in (10.0, 100.0) and set(scores) == {10.0, 100.0}
    assert min(scores.values()) < baseline


def test_features_from_a_race_state():
    state = state_at(20, scripted_race(pace=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5]))
    x = features(state, np.linspace(-0.2, 0.3, N), np.arange(1, N + 1, dtype=float),
                 np.zeros(N), PROFILE)
    assert x.shape == (N, len(FEATURES))
    col = {name: i for i, name in enumerate(FEATURES)}
    assert x[0, col["position_share"]] == pytest.approx(1 / N)
    assert x[0, col["gap_ahead"]] == 60.0 and x[-1, col["gap_behind"]] == 60.0
    assert x[0, col["distance_done"]] == pytest.approx(20 / 30)
    assert (x[:, col["owes_compound"]] == 0).all()  # medium then hard
    assert x[:, col["pace_this_race"]].mean() == pytest.approx(0.0)


def test_score_probabilities_matches_score():
    positions = np.array([[1, 2, 3, 4], [2, 1, 3, 4], [1, 3, 2, 4]])
    retired = np.zeros_like(positions, dtype=bool)
    actual = [1, 2, 3, 999]
    a = live_backtest.score(positions, retired, actual)
    b = live_backtest.score_probabilities(
        ((positions == 1)).mean(0), (positions <= 3).mean(0), positions.mean(0), actual)
    assert a == b
