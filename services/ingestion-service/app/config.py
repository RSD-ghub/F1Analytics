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

    # Where the live feed's recordings are kept (one text file per race, as
    # FastF1's live client writes it). Kept after the race so it can be
    # replayed through the live path; see services/live_feed.py.
    live_recordings_dir: str = "live-recordings"
    # Start the live feed this many minutes before each race's calendar start,
    # without anyone pressing Start live (services/live_autostart.py). Needs
    # F1TV connected. 0 turns it off.
    live_autostart_minutes: int = 10

    # Periodic refresh of the current season, in hours.
    auto_refresh_hours: int = 24

    # How often to look for a newly published FIA starting grid. Minutes, not
    # hours: the document lands a few hours after qualifying and the post-quali
    # forecast locks shortly after, so the useful window is narrow.
    grid_check_minutes: int = 30

    # Sessions per hour a long backfill is allowed to fetch. FastF1 permits 500
    # API calls an hour and a full-depth session spends several, so 55 keeps a
    # single pass under the ceiling instead of exhausting it in minutes and
    # needing four passes across four hours. 0 disables pacing.
    backfill_sessions_per_hour: int = 55

    # Paddock news. Outlets refresh their feeds every few minutes during a race
    # weekend, but nothing downstream needs a headline within minutes of it
    # being written, and a polite interval is part of reading a feed at all.
    # 0 disables the scheduled read; POST /news/refresh still works.
    news_refresh_minutes: int = 30
    # Headlines older than this are dropped. Long enough to cover a race week
    # and the fortnight either side of it; the outlets keep the archive.
    news_retention_days: int = 60

    # Upstream schedule/results API used alongside FastF1 for forward-looking data.
    ergast_base_url: str = "https://api.jolpi.ca/ergast"
    # Laps, stints, pit stops, weather and race control for sessions F1's live
    # timing will not serve without an F1TV login (from October 2026). Free,
    # no key, 2023 onwards. Empty turns the fallback off.
    openf1_base_url: str = "https://api.openf1.org/v1"

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
