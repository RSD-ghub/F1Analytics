"""FastAPI dependency wiring for scoring-service."""

from functools import lru_cache

from app import db
from app.config import Settings, get_settings
from app.services.clients import IngestionClient, PredictionClient
from app.services.reconciler import Reconciler
from app.services.storage import ScoringStore


@lru_cache
def get_ingestion() -> IngestionClient:
    return IngestionClient(get_settings().ingestion_service_url)


@lru_cache
def get_predictions() -> PredictionClient:
    return PredictionClient(get_settings().prediction_service_url)


def get_store() -> ScoringStore:
    return ScoringStore(db.db())


def get_reconciler() -> Reconciler:
    return Reconciler(
        ingestion=get_ingestion(),
        predictions=get_predictions(),
        store=get_store(),
    )
