"""Base settings every service extends.

Each service subclasses ``ServiceSettings`` and pins its own ``service_name`` and
``mongo_database`` — a service may only ever talk to its own database, which is how
the per-service data ownership boundary is actually enforced.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Configuration common to all four services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identity
    service_name: str = "f1-service"
    environment: str = "development"
    log_level: str = "INFO"

    # Storage — mongo_database is overridden per service and must never be shared.
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "f1_scratch"
    mongo_timeout_ms: int = 5000
