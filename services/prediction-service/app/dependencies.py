"""FastAPI dependency wiring for prediction-service."""

import logging
from functools import lru_cache
from typing import Optional

from app import db
from app.config import Settings, get_settings
from app.services.ingestion_client import IngestionClient
from app.services.model import ModelWeightsMissing, RaceModel
from app.services.neural_model import NeuralRaceModel
from app.services.predictor import Predictor
from app.services.storage import PredictionStore


@lru_cache
def get_client() -> IngestionClient:
    settings: Settings = get_settings()
    return IngestionClient(settings.ingestion_service_url)


@lru_cache
def get_model() -> RaceModel:
    """One model per process, loaded from the fitted weights artifact.

    Deliberately no fallback. If the artifact is missing this raises and the
    service fails to serve forecasts — which is correct: predictions from
    untrained weights are indistinguishable in shape from trained ones, so a
    silent fallback would publish guesses into a permanent public record.
    """
    settings: Settings = get_settings()
    return RaceModel.load(runs=settings.prediction_runs)


@lru_cache
def get_neural_model() -> Optional[NeuralRaceModel]:
    """The neural race model, or None when none has been trained.

    Unlike the linear model, a missing artifact is survivable: the network
    places nothing until it has been promoted, so its absence costs the race
    simulation, which says so, and nothing else.
    """
    settings: Settings = get_settings()
    try:
        return NeuralRaceModel.load(runs=settings.race_simulation_runs)
    except ModelWeightsMissing as exc:
        logging.getLogger(__name__).info("race simulation unavailable: %s", exc)
        return None
    except ValueError as exc:
        # Fitted on a different feature set — an artifact from before a feature
        # was added. Survivable for the same reason a missing one is.
        logging.getLogger(__name__).warning(
            "race simulation unavailable: neural artifact is stale: %s", exc)
        return None


@lru_cache
def get_lap_engine():
    """The lap-by-lap simulator, or None when it has not been fitted."""
    from app.services.lap_simulation import LapRaceEngine

    try:
        return LapRaceEngine.load()
    except ModelWeightsMissing as exc:
        logging.getLogger(__name__).info("lap simulator unavailable: %s", exc)
        return None


def get_store() -> PredictionStore:
    return PredictionStore(db.db())


def get_predictor() -> Predictor:
    return Predictor(
        client=get_client(), store=get_store(), model=get_model(),
        final_grid_model=get_neural_model(),
        lap_engine=get_lap_engine(),
    )
