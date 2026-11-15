"""Served forecasts see the qualifying and practice the model was trained on."""

import asyncio

from app.models.schemas import LockWindow
from app.services import predictor as predictor_module
from app.services.ingestion_client import IngestionUnavailable
from app.services.predictor import Predictor


class Client:
    def __init__(self, down=()):
        self.down = set(down)

    async def season_qualifying(self, season):
        if "quali" in self.down:
            raise IngestionUnavailable("down")
        return ["quali-{}".format(season)]

    async def season_practice(self, season):
        if "practice" in self.down:
            raise IngestionUnavailable("down")
        return ["practice-{}".format(season)]


def test_qualifying_from_two_seasons_and_this_seasons_practice():
    quali, practice = asyncio.run(Predictor(Client(), None, None)._pace_history(2026))
    assert quali == ["quali-2025", "quali-2026"] and practice == ["practice-2026"]


def test_unavailable_means_neutral_not_an_error():
    quali, practice = asyncio.run(
        Predictor(Client(down=("quali", "practice")), None, None)._pace_history(2026))
    assert quali == [] and practice == []


def test_they_reach_the_feature_builder(monkeypatch):
    seen = {}

    def build(results, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    class Full(Client):
        async def season_results(self, season):
            return []

    predictor = Predictor(Full(), None, None)

    async def no_grid(*a, **k):
        return []

    monkeypatch.setattr(predictor, "_maybe_grid", no_grid)
    monkeypatch.setattr(predictor_module.feature_builder, "build_snapshot", build)
    try:
        asyncio.run(predictor._prepare(2026, 21, LockWindow.PRE_QUALI))
    except RuntimeError:
        pass
    assert seen["quali_history"] == ["quali-2025", "quali-2026"]
    assert seen["practice"] == ["practice-2026"]
