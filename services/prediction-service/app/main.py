"""prediction-service — point-in-time features, locked forecasts, championship odds.

Internal-only: not published to the host in docker-compose, reached solely by
core-api over the compose network.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import get_settings
from app.routers import predictions
from app.services.storage import PredictionStore
from f1_common.health import build_health_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect(settings.mongo_uri, settings.mongo_database, settings.mongo_timeout_ms)

    # The unique index on (season, round, window) is what enforces prediction
    # immutability, so a failure here is worth shouting about — but it must not
    # stop the service starting, or a transient Mongo blip at boot would take
    # the whole forecast API down.
    try:
        await PredictionStore(db.db()).ensure_indexes()
    except Exception:
        logger.exception(
            "index creation failed; prediction immutability is NOT enforced "
            "until indexes exist"
        )

    yield
    db.close()


app = FastAPI(
    title="F1 Prediction Service",
    version="0.1.0",
    description="Point-in-time features, calibrated forecasts, championship simulation.",
    lifespan=lifespan,
)

app.include_router(
    build_health_router(settings.service_name, settings.environment, db.client)
)
app.include_router(predictions.router)
app.include_router(predictions.championship_router)
