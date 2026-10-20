"""The lap-trained pace network: relative pace, nonlinear, safe on constant columns."""

import numpy as np

from app.training.pace_network import PaceNetwork, centred


def races(count, seed):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(count * 10, 3))
    x[:, 2] = 0.0  # a column that does not exist yet, like practice before 2018
    pace = 0.8 * x[:, 0] * x[:, 1] + 0.3 * x[:, 0] + rng.normal(0, 0.1, len(x))
    groups = np.repeat(np.arange(count), 10)
    return x, pace + groups * 5.0, groups  # each race at its own pace level


def test_it_learns_what_a_straight_line_cannot():
    x, y, g = races(150, 0)
    xt, yt, gt = races(60, 1)
    target = centred(yt, gt)
    net = PaceNetwork(hidden=8, l2=1.0).fit(x, y, g)
    line = PaceNetwork(hidden=1, l2=1e6).fit(x, y, g)
    net_error = np.mean((net.predict(xt, gt) - target) ** 2)
    line_error = np.mean((line.predict(xt, gt) - target) ** 2)
    assert net_error < 0.5 * line_error


def test_predictions_are_relative_within_each_race():
    x, y, g = races(40, 2)
    prediction = PaceNetwork().fit(x, y, g).predict(x, g)
    for group in np.unique(g):
        assert abs(prediction[g == group].mean()) < 1e-9


def test_a_column_constant_in_training_cannot_explode_later():
    x, y, g = races(60, 3)
    net = PaceNetwork().fit(x, y, g)
    later = x.copy()
    later[:, 2] = 0.03  # the column starts carrying values
    assert np.allclose(net.predict(later, g), net.predict(x, g))


def test_it_survives_a_round_trip_through_json():
    import json
    x, y, g = races(40, 4)
    net = PaceNetwork().fit(x, y, g)
    again = PaceNetwork.from_dict(json.loads(json.dumps(net.to_dict())))
    assert np.allclose(again.predict(x, g), net.predict(x, g))
