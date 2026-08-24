"""FastAPI dependency wiring.

The source and runner are built per request from module-level singletons: the
FastF1 source holds an on-disk cache handle worth reusing across requests, while
the store is a thin wrapper over the shared Motor client and is cheap to
construct.
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Tuple

from app import db
from app.config import Settings, get_settings
from app.services.fastf1_source import FastF1Source
from app.services.ingest_runner import IngestRunner
from app.services.storage import IngestionStore
from f1_common.llm import LLMClient, build_client


@lru_cache
def get_source() -> FastF1Source:
    """One source per process — the FastF1 cache is enabled once on first use."""
    settings: Settings = get_settings()
    return FastF1Source(cache_dir="{}/cache".format(settings.dataset_root))


def get_store() -> IngestionStore:
    return IngestionStore(db.db())


@lru_cache
def get_llm() -> LLMClient:
    """One client per process. Returns a no-op client when unconfigured."""
    settings: Settings = get_settings()
    return build_client(
        provider=settings.llm_provider,
        api_key=settings.tinker_api_key,
        base_url=settings.tinker_base_url,
        model=settings.inkling_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def get_runner() -> IngestRunner:
    settings: Settings = get_settings()
    return IngestRunner(
        source=get_source(),
        store=get_store(),
        llm=get_llm(),
        extract_with_llm=settings.llm_signal_extraction,
    )


def season_range(settings: Settings) -> Tuple[int, int]:
    """Default backfill span: the configured floor through the current season."""
    return settings.season_floor, datetime.now(timezone.utc).year
