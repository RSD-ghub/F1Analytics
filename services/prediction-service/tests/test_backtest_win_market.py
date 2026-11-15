"""The pre-quali win-market re-test scores what it says it scores."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import backtest_win_market as wm  # noqa: E402

from app.services.model import feature_names
from app.training.plackett_luce import RaceObservation

PRE = feature_names(with_grid=False)


def races(signal, seed=0, seasons=range(2016, 2022), per_season=10, field=12):
    """Races decided by column 0 with weight ``signal``, plus Gumbel noise."""
    rng = np.random.default_rng(seed)
    out = []
    for season in seasons:
        for r in range(2, per_season + 2):
            x = rng.normal(size=(field, len(PRE)))
            order = list(np.argsort(-(signal * x[:, 0] + rng.gumbel(size=field))))
            out.append(RaceObservation(x, order, season=season, round_number=r))
    return out


def test_a_uniform_guess_scores_zero_and_a_sure_thing_scores_full_marks():
    race = RaceObservation(np.zeros((4, 1)), [2, 0, 1, 3])
    brier, uniform, log_loss, uniform_log = wm.score_race(np.full(4, 0.25), race)
    assert abs(brier - uniform) < 1e-12 and abs(log_loss - uniform_log) < 1e-12
    sure = wm.score_race(np.array([0, 0, 1.0, 0]), race)
    assert sure[0] == 0.0 and sure[2] == 0.0


def test_the_win_chances_are_a_softmax_and_temperature_flattens_them():
    race = RaceObservation(np.array([[2.0], [0.0], [-1.0]]), [0, 1, 2])
    sharp = wm.win_probabilities(np.array([1.0]), race)
    flat = wm.win_probabilities(np.array([1.0]), race, temperature=3.0)
    assert abs(sharp.sum() - 1) < 1e-12 and abs(flat.sum() - 1) < 1e-12
    assert sharp[0] > flat[0] > 1 / 3


def test_a_real_signal_shows_skill_and_earns_publish():
    seasons = [2019, 2020, 2021]
    summary = wm.run_observations({"pre_quali": races(3.0)}, seasons, 2016, 0.6)
    pre = summary["pre_quali"]
    assert [s["season"] for s in pre["seasons"]] == seasons
    assert pre["races"] == 30
    assert pre["pooled"]["brier"]["low"] > 0
    assert summary["verdict"].startswith("publish")


def test_noise_does_not_earn_publish():
    summary = wm.run_observations({"pre_quali": races(0.0, seed=3)}, [2019, 2020, 2021],
                                  2016, 0.6)
    assert summary["verdict"].startswith("keep it off")


def test_the_verdict_wants_every_season_above_zero():
    good = {"skill": 0.1, "low": 0.02, "high": 0.2}
    bad = {"skill": -0.05, "low": -0.2, "high": 0.1}
    window = {"pooled": {"brier": good},
              "seasons": [{"season": 2024, "brier": good}, {"season": 2025, "brier": bad}]}
    assert wm.verdict(window) == "keep it off: at or below zero in 2025"
