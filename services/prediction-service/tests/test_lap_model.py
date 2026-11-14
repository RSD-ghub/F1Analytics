"""The lap model as the live forecast's pace: quicker cars get a quicker pace."""

import json

import numpy as np

from app.models.schemas import DriverFeatures
from app.services import live_forecast
from app.services.ingestion_client import RaceLap
from app.services.lap_model import LapModel
from app.training.lap_time_network import (LAP_FEATURES, LapTimeNetwork, race_rows)
from app.services.model import feature_vector


def _drivers(n=6):
    return [DriverFeatures(driver="D{}".format(i), team="T{}".format(i // 2), grid_position=i + 1)
            for i in range(n)]


def _laps(offsets, laps=20, compound="MEDIUM"):
    out = []
    for i, offset in enumerate(offsets):
        for n in range(1, laps + 1):
            out.append(RaceLap(season=2024, round=1, driver="D{}".format(i), lap=n,
                               lap_time_seconds=90.0 + offset + (5 if n == 1 else 0),
                               compound=compound, stint=1, position=i + 1))
    return out


def _model(laps, drivers):
    rows = race_rows(2024, 1, laps, 20)
    vectors = {d.driver: feature_vector(d, with_grid=True) for d in drivers}
    x = np.array([np.hstack([rows.x[i], vectors[d]]) for i, d in enumerate(rows.drivers)])
    return LapModel(LapTimeNetwork(hidden=()).fit(x, rows.y))


PROFILE = {"tyres": {c: {"offset": 0.0, "wear": 0.0} for c in ("SOFT", "MEDIUM", "HARD")}}


def test_pace_orders_cars_by_their_running():
    drivers = _drivers()
    laps = _laps([0.0, 0.4, 0.8, 1.2, 1.6, 2.0])
    pace = _model(laps, drivers).pace(drivers, laps, 12, 20, PROFILE, 2024, 1)
    assert np.all(np.isfinite(pace))
    assert np.all(np.diff(pace) > 0)


def test_no_clean_lap_or_wet_tyres_means_no_override():
    drivers = _drivers()
    laps = _laps([0.0, 0.4, 0.8, 1.2, 1.6, 2.0])
    model = _model(laps, drivers)
    assert np.all(np.isnan(model.pace(drivers, laps, 1, 20, PROFILE, 2024, 1)))
    wet = _laps([0.0, 0.4, 0.8, 1.2, 1.6, 2.0], compound="INTERMEDIATE")
    assert np.all(np.isnan(model.pace(drivers, wet, 12, 20, PROFILE, 2024, 1)))


def test_override_keeps_the_posterior_level_and_skips_gaps():
    pace = np.array([0.0, 0.2, 0.4, 0.6])
    override = np.array([10.0, np.nan, 12.0, 11.0])
    out = live_forecast.apply_override(pace, override, np.zeros(4, bool))
    assert out[1] == 0.2
    assert np.isclose(out[[0, 2, 3]].mean(), pace[[0, 2, 3]].mean())
    assert np.isclose(out[2] - out[0], 2.0)
    assert np.array_equal(live_forecast.apply_override(pace, None, np.zeros(4, bool)), pace)


def test_load_needs_an_enabled_linear_model(tmp_path):
    net = LapTimeNetwork(hidden=()).fit(np.random.default_rng(0).normal(size=(50, 3)),
                                        np.zeros(50))
    path = tmp_path / "lap.json"
    path.write_text(json.dumps({"linear": net.to_dict(), "linear_enabled": False}))
    assert LapModel.load(str(path)) is None
    path.write_text(json.dumps({"linear": net.to_dict(), "linear_enabled": True}))
    assert isinstance(LapModel.load(str(path)), LapModel)
    assert LapModel.load(str(tmp_path / "missing.json")) is None
