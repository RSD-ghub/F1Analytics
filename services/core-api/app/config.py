"""core-api configuration.

The only publicly reachable service. Everything the browser talks to comes
through here.
"""

from functools import lru_cache
from typing import List

from f1_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "core-api"
    mongo_database: str = "f1_core"
    port: int = 8000

    # Downstream internal services.
    ingestion_service_url: str = "http://ingestion-service:8001"
    prediction_service_url: str = "http://prediction-service:8002"
    scoring_service_url: str = "http://scoring-service:8003"
    downstream_timeout_seconds: float = 10.0

    # Auth. jwt_secret has no usable default on purpose — a weak signing key is
    # worse than a missing one, so it must be supplied via the environment.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # LLM (Inkling via Tinker). Held behind a swappable client interface so the
    # provider can change without touching domain code.
    llm_provider: str = "tinker"
    tinker_api_key: str = ""
    # Verified against Tinker's docs: the base already ends in /api/v1,
    # so the client appends /chat/completions and nothing more.
    tinker_base_url: str = (
        "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"
    )
    inkling_model: str = "thinkingmachines/Inkling"
    # Bernie rephrases supplied facts rather than solving problems, so
    # Tinker's 0.9 default buys latency and cost for nothing.
    inkling_reasoning_effort: str = "medium"

    # Browser origins allowed to call this API directly (nginx-fronted deploys
    # are same-origin and do not need an entry here).
    cors_allow_origins: List[str] = ["http://localhost:3000", "http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
