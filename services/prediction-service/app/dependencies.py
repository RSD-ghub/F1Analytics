"""FastAPI dependency wiring for prediction-service."""

from functools import lru_cache

from app import db
from app.config import Settings, get_settings
from app.services.ingestion_client import IngestionClient
from app.services.model import RaceModel
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


def get_store() -> PredictionStore:
    return PredictionStore(db.db())


def get_predictor() -> Predictor:
    return Predictor(client=get_client(), store=get_store(), model=get_model())
