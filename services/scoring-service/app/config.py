"""scoring-service configuration."""

from functools import lru_cache

from f1_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "scoring-service"
    mongo_database: str = "f1_scoring"
    port: int = 8003

    # Actual race outcomes come from ingestion; locked forecasts from prediction.
    ingestion_service_url: str = "http://ingestion-service:8001"
    prediction_service_url: str = "http://prediction-service:8002"

    # Number of buckets used when aggregating a calibration curve.
    calibration_buckets: int = 10

    # How often to sweep for races that have finished and can now be scored.
    # Thirty minutes rather than hours: results land at an unpredictable delay
    # after the flag — Monza's took most of a day — and a forecast that is
    # locked but unscored looks exactly like one being quietly withheld.
    reconcile_check_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
