"""The lap-time network: rows that know only the past, and a fit that is right."""

import numpy as np
from scipy.optimize import check_grad

from app.services.ingestion_client import RaceLap
from app.training.lap_time_network import (
    LAP_FEATURES,
    LapTimeNetwork,
    centred,
    centred_rmse,
    race_rows,
    race_weather,
)

COL = {name: i for i, name in enumerate(LAP_FEATURES)}


def _race(pace=None, laps=30, safety_car=(), compound="MEDIUM", stop_lap=15, season=2024):
    """Six cars; car i is pace[i] seconds a lap off the base. One stop each."""
    pace = pace or [0.0, 0.3, 0.6, 0.9, 1.2, 1.5]
    rows = []
    for i, offset in enumerate(pace):
        for n in range(1, laps + 1):
            stint = 1 if n <= stop_lap else 2
            age = n if stint == 1 else n - stop_lap
            time = 90.0 + offset + 0.05 * age - 0.03 * n
            if n == 1:
                time += 5.0
            if n in safety_car:
                time = 125.0
            if n in (stop_lap, stop_lap + 1):
                time += 11.0
            rows.append(RaceLap(season=season, round=1, driver="D{}".format(i), lap=n,
                                lap_time_seconds=time, compound=compound, stint=stint,
                                position=i + 1))
    return rows


def _row(rows, lap, driver):
    return [i for i in range(len(rows.y)) if rows.laps[i] == lap and rows.drivers[i] == driver][0]


def test_rows_cover_clean_laps_only():
    rows = race_rows(2024, 1, _race(safety_car=(8, 9)), 30, archetype="power")
    assert rows is not None
    assert 1 not in rows.laps                            # the start
    assert 8 not in rows.laps and 9 not in rows.laps     # behind the safety car
    assert 15 not in rows.laps and 16 not in rows.laps   # in- and out-laps
    assert rows.x.shape == (len(rows.y), len(LAP_FEATURES))
    assert np.all(rows.x[:, COL["archetype_power"]] == 1.0)


def test_live_pace_uses_only_laps_already_run():
    rows = race_rows(2024, 1, _race(), 30)
    first = rows.laps == rows.laps.min()
    assert np.all(rows.x[first, COL["live_laps"]] == 0)
    assert np.all(rows.x[first, COL["live_pace"]] == 0)
    fastest, slowest = _row(rows, 12, "D0"), _row(rows, 12, "D5")
    assert rows.x[fastest, COL["live_pace"]] < 0 < rows.x[slowest, COL["live_pace"]]


def test_a_later_lap_cannot_change_an_earlier_row():
    base = _race()
    changed = [lap.model_copy(update={"lap_time_seconds": lap.lap_time_seconds + 2.0})
               if lap.driver == "D2" and lap.lap == 20 else lap for lap in base]
    a = race_rows(2024, 1, base, 30)
    b = race_rows(2024, 1, changed, 30)
    # Every input up to and including lap 20 is the same; only later ones see it.
    assert np.allclose(a.x[a.laps <= 20], b.x[b.laps <= 20])
    assert not np.allclose(a.x[a.laps == 21], b.x[b.laps == 21])


def test_traffic_and_neutralisation_features():
    rows = race_rows(2024, 1, _race(safety_car=(8, 9)), 30)
    after = [i for i in range(len(rows.y)) if rows.laps[i] == 11]
    assert all(rows.x[i, COL["since_neutralised"]] == 2 for i in after)
    leader, second = _row(rows, 11, "D0"), _row(rows, 11, "D1")
    assert rows.x[leader, COL["gap_ahead"]] == 5.0       # nobody ahead: the cap
    assert 0 < rows.x[second, COL["gap_ahead"]] < 5.0
    assert np.all(rows.x[:, COL["traffic_known"]] == 1.0)


def test_unknown_compound_and_missing_weather_are_flagged():
    rows = race_rows(2015, 1, _race(compound="Unknown", season=2015), 30)
    assert np.all(rows.x[:, COL["compound_unknown"]] == 1.0)
    assert np.all(rows.x[:, COL["weather_known"]] == 0.0)
    assert np.all(rows.x[:, COL["era_2017"]] == 0.0)


def test_race_weather_summary():
    summary = race_weather([
        {"season": 2024, "round": 1, "track_temp": 40.0, "air_temp": 25.0, "rainfall": False},
        {"season": 2024, "round": 1, "track_temp": 30.0, "air_temp": 23.0, "rainfall": True},
    ])
    assert summary[(2024, 1)] == {"track_temp": 35.0, "air_temp": 24.0, "rain": 0.5}


def _data(count, seed):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(count, 4))
    y = 0.5 * x[:, 0] + np.tanh(2 * x[:, 1]) * x[:, 2] + rng.normal(0, 0.05, count)
    return x, y


def test_deeper_network_beats_a_straight_line_on_an_interaction():
    x, y = _data(3000, 0)
    xt, yt = _data(1000, 1)
    net = LapTimeNetwork(hidden=(16, 8), l2=1e-2, max_iter=800).fit(x, y)
    line = LapTimeNetwork(hidden=()).fit(x, y)
    assert np.mean((net.predict(xt) - yt) ** 2) < 0.3 * np.mean((line.predict(xt) - yt) ** 2)


def test_gradient_matches_finite_differences():
    x, y = _data(200, 2)
    net = LapTimeNetwork(hidden=(5, 3), l2=0.5)
    net.mean, net.std, net.inputs = x.mean(0), x.std(0), x.shape[1]
    xs = net._scale(x)
    share = np.random.default_rng(5).uniform(0.5, 1.5, len(y))
    share /= share.sum()
    size = sum(int(np.prod(s)) for s in net._shapes())
    point = np.random.default_rng(3).normal(0, 0.3, size)
    error = check_grad(lambda f: net._objective(f, xs, y, share)[0],
                       lambda f: net._objective(f, xs, y, share)[1], point)
    assert error < 1e-5


def test_linear_shape_and_round_trip():
    x, y = _data(500, 4)
    for model in (LapTimeNetwork(hidden=()).fit(x, y), LapTimeNetwork(hidden=(6, 4)).fit(x, y)):
        again = LapTimeNetwork.from_dict(model.to_dict())
        assert np.allclose(again.predict(x), model.predict(x))


def test_a_column_constant_in_training_cannot_explode_later():
    x, y = _data(500, 6)
    x[:, 3] = 0.0   # weather before 2018
    net = LapTimeNetwork(hidden=(6,)).fit(x, y)
    later = x.copy()
    later[:, 3] = 30.0
    assert np.allclose(net.predict(later), net.predict(x))


def test_centred_scores_ignore_the_level_of_each_lap():
    groups = np.array([1, 1, 2, 2])
    target = np.array([1.0, 2.0, 10.0, 12.0])
    shifted = target + np.array([5.0, 5.0, -3.0, -3.0])
    assert centred_rmse(shifted, target, groups) == 0.0
    assert np.allclose(centred(target, groups), [-0.5, 0.5, -1.0, 1.0])


def test_simulator_prediction_learns_pace_from_the_race_so_far():
    from app.training.lap_time_network import simulator_prediction

    rows = race_rows(2024, 1, _race(), 30)
    profile = {"typical_lap": 90.0,
               "tyres": {"MEDIUM": {"offset": 0.0, "wear": 0.05}}}
    blind = {d: 0.0 for d in set(rows.drivers)}   # a prior that knows nothing
    prediction = simulator_prediction(rows, blind, profile)
    early = rows.laps == rows.laps.min()
    late = rows.laps == 28
    groups = rows.laps
    early_error = centred_rmse(prediction[early], rows.y[early], groups[early])
    late_error = centred_rmse(prediction[late], rows.y[late], groups[late])
    assert late_error < 0.3 * early_error
