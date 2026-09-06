"""prediction-service configuration."""

from functools import lru_cache

from f1_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "prediction-service"
    mongo_database: str = "f1_prediction"
    port: int = 8002

    # Read-only mount of the dataset ingestion-service maintains. Reading Parquet
    # keeps the fast pandas path and avoids coupling to ingestion's Mongo schema.
    data_dir: str = "fastf1-data"

    # Needed to check data completeness before a lock window fires.
    ingestion_service_url: str = "http://ingestion-service:8001"

    # Monte Carlo championship simulation.
    simulation_runs: int = 20000

    # Lock windows, expressed as hours before the race start time.
    pre_quali_lock_hours_before: int = 72
    post_quali_lock_hours_before: int = 18

    # The confirmed-grid window. Minutes, not hours: the FIA publishes the final
    # starting grid at exactly T-1h — measured at +1.0h on seven of seven events
    # across two seasons and six timezones — so this window is the sliver
    # between that publication and the race. 45 minutes leaves a 15-minute
    # margin after the document appears.
    final_grid_lock_minutes_before: int = 45

    # Sampling iterations for a single race forecast. Higher is smoother but
    # slower; 20k puts the Monte Carlo error on a p_win well below a point.
    prediction_runs: int = 20000

    # The calibration knob. Raising it flattens probabilities toward uniform.
    # Fitted on the 2024 season — see DEFAULT_NOISE_SCALE in services/model.py
    # for the sweep this came from. Changing it changes published probabilities,
    # so MODEL_VERSION must be bumped alongside any change.
    model_noise_scale: float = 2.0

    # Earliest season with usable history.
    season_floor: int = 2010

    # How often to check whether a lock window has opened. Now driven by the
    # tightest window rather than the loosest: the final-grid window is only 45
    # minutes wide and opens 15 minutes after the document it depends on, so a
    # 15-minute tick could give it as little as one attempt. Five minutes gives
    # it a real chance to retry while staying cheap.
    lock_check_minutes: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
