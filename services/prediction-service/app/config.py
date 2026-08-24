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


@lru_cache
def get_settings() -> Settings:
    return Settings()
