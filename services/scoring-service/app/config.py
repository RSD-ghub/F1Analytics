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

    # Watchdog: alerts when a lock window passes with no forecast, or a race
    # goes unscored. See app/services/watchdog.py. 0 disables it.
    watchdog_check_minutes: int = 60
    # Where alerts go. Empty means they are only logged (as ERROR lines).
    # An ntfy.sh topic URL (https://ntfy.sh/<your-topic>) pushes to a phone
    # with no account; "json" format suits Slack and Discord webhooks.
    alert_webhook_url: str = ""
    alert_webhook_format: str = "text"
    # Grand Prix windows that should lock every weekend. Comma-separated.
    watchdog_expected_windows: str = "pre_quali,post_quali,final_grid"


@lru_cache
def get_settings() -> Settings:
    return Settings()
