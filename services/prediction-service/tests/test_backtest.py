"""Season-by-season backtests and paired bootstrap intervals."""

import numpy as np

from app.training import backtest
from app.training.plackett_luce import RaceObservation

NAMES = ["signal", "noise"]


def history(seed=0, seasons=range(2014, 2022), races=12):
    rng = np.random.default_rng(seed)
    out = []
    for season in seasons:
        for r in range(1, races + 1):
            x = rng.normal(size=(12, 2))
            order = list(np.argsort(-(1.5 * x[:, 0] + rng.gumbel(size=12))))
            out.append(RaceObservation(x, order, season=season, round_number=r))
    return out


def test_each_season_is_scored_by_a_fit_on_earlier_seasons_only():
    obs = history()
    per_race = backtest.rolling_origin(obs, NAMES, ["signal"], range(2018, 2022))
    assert {season for season, _ in per_race} == {2018, 2019, 2020, 2021}
    assert len(per_race) == 4 * 12
    assert set(backtest.by_season(per_race)) == {2018, 2019, 2020, 2021}


def test_a_real_feature_clears_zero():
    obs = history()
    seasons = range(2016, 2022)
    without = backtest.rolling_origin(obs, NAMES, ["noise"], seasons)
    with_signal = backtest.rolling_origin(obs, NAMES, ["noise", "signal"], seasons)
    result = backtest.paired_bootstrap(without, with_signal)
    assert result["low"] > 0 and result["better_share"] > 0.6


def test_a_noise_feature_does_not():
    obs = history(seed=1)
    seasons = range(2016, 2022)
    without = backtest.rolling_origin(obs, NAMES, ["signal"], seasons)
    with_noise = backtest.rolling_origin(obs, NAMES, ["signal", "noise"], seasons)
    result = backtest.paired_bootstrap(without, with_noise)
    assert result["low"] < 0 < result["high"] or result["high"] <= 0
