"""ingestion-service configuration."""

from functools import lru_cache

from f1_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "ingestion-service"
    mongo_database: str = "f1_ingestion"
    port: int = 8001

    # Where the Parquet/JSON dataset lives. This is a derived read-optimisation
    # for prediction-service, not a source of truth — Mongo is authoritative.
    dataset_root: str = "fastf1-data"

    # Earliest season the backfill will reach for.
    season_floor: int = 2010

    # Periodic refresh of the current season, in hours.
    auto_refresh_hours: int = 24

    # How often to look for a newly published FIA starting grid. Minutes, not
    # hours: the document lands a few hours after qualifying and the post-quali
    # forecast locks shortly after, so the useful window is narrow.
    grid_check_minutes: int = 30

    # Upstream schedule/results API used alongside FastF1 for forward-looking data.
    ergast_base_url: str = "https://api.jolpi.ca/ergast"

    # LLM signal extraction. Enrichment only — with no key configured, ingestion
    # runs unchanged and simply produces rule-based signals alone.
    llm_provider: str = "tinker"
    tinker_api_key: str = ""
    tinker_base_url: str = (
        "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"
    )
    inkling_model: str = "thinkingmachines/Inkling"
    llm_timeout_seconds: float = 60.0
    #: Whether to spend LLM calls during ingest at all.
    llm_signal_extraction: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
