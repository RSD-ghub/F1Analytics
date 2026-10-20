"""Season-by-season backtests and paired bootstrap intervals."""

import numpy as np
import pytest

from app.training import backtest
from app.training.plackett_luce import RaceObservation, mean_log_likelihood

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


def _race(season, round_number, strengths, rng):
    """A field whose finishing order is drawn from Plackett-Luce on ``strengths``."""
    features = np.column_stack([strengths, rng.normal(size=len(strengths))])
    noisy = strengths + rng.gumbel(size=len(strengths))
    order = list(np.argsort(-noisy))
    return RaceObservation(features, order, season=season, round_number=round_number)


@pytest.fixture
def seasons():
    rng = np.random.default_rng(3)
    races = []
    for season in range(2015, 2021):
        for round_number in range(1, 13):
            races.append(_race(season, round_number, rng.normal(size=12) * 1.5, rng))
    return races


def test_uniform_matches_the_training_baseline(seasons):
    from app.training.plackett_luce import uniform_baseline_log_likelihood

    per_race = [backtest.uniform_log_likelihood(r) for r in seasons]
    assert np.mean(per_race) == pytest.approx(uniform_baseline_log_likelihood(seasons))


def test_race_log_likelihood_matches_the_mean(seasons):
    weights = np.array([1.0, 0.2])
    per_race = [backtest.race_log_likelihood(weights, r) for r in seasons]
    assert np.mean(per_race) == pytest.approx(mean_log_likelihood(weights, seasons))


def test_folds_train_only_on_earlier_seasons(seasons):
    seen = {}

    def spy(train, names):
        seen.setdefault("seasons", []).append({o.season for o in train})
        return lambda race: 0.0

    folds = backtest.rolling_folds(seasons, ["pace", "noise"], [2018, 2019, 2020], {"spy": spy})
    assert [f.season for f in folds] == [2018, 2019, 2020]
    for fold, trained_on in zip(folds, seen["seasons"]):
        assert max(trained_on) < fold.season
        assert len(fold.scores["spy"]) == len(fold.uniform) == 12


def test_each_fold_scales_on_its_own_training_races(seasons):
    captured = []

    def spy(train, names):
        captured.append(np.vstack([o.features for o in train]))
        return lambda race: 0.0

    backtest.rolling_folds(seasons, ["pace", "noise"], [2018, 2020], {"spy": spy})
    for stacked in captured:
        assert stacked.mean(axis=0) == pytest.approx([0.0, 0.0], abs=1e-9)
        assert stacked.std(axis=0) == pytest.approx([1.0, 1.0])


def test_folds_leave_the_input_untouched(seasons):
    before = [r.features.copy() for r in seasons]
    backtest.rolling_folds(seasons, ["pace", "noise"], [2019], {"zero": lambda t, n: lambda r: 0.0})
    for original, race in zip(before, seasons):
        assert np.array_equal(original, race.features)


def test_a_real_signal_beats_uniform_with_an_interval_above_zero(seasons):
    from app.training import plackett_luce

    def linear(train, names):
        weights = plackett_luce.fit(train, names)
        vector = np.array([weights[n] for n in names])
        return lambda race: backtest.race_log_likelihood(vector, race)

    folds = backtest.rolling_folds(seasons, ["pace", "noise"], [2019, 2020], {"linear": linear})
    point, low, high = backtest.improvement_interval(*backtest.pooled(folds, "linear"))
    assert low > 0
    assert low <= point <= high


def test_bootstrap_interval_contains_the_mean_and_is_reproducible():
    values = np.random.default_rng(0).normal(1.0, 2.0, size=50)
    first = backtest.bootstrap_interval(values)
    assert first == backtest.bootstrap_interval(values)
    mean, low, high = first
    assert low < mean < high


def test_paired_difference_detects_a_consistent_edge_and_ignores_noise():
    rng = np.random.default_rng(1)
    champion = rng.normal(-37, 4, size=80)
    better = backtest.paired_difference(champion + 0.3 + rng.normal(0, 0.1, 80), champion)
    assert better["low"] > 0 and better["share_better"] > 0.99
    assert backtest.verdict(better).startswith("challenger is better")

    noise = backtest.paired_difference(champion + rng.normal(0, 1.0, 80), champion)
    assert noise["low"] < 0 < noise["high"]
    assert backtest.verdict(noise).startswith("no measurable difference")


def test_paired_difference_requires_the_same_races():
    with pytest.raises(ValueError):
        backtest.paired_difference([1.0, 2.0], [1.0])
