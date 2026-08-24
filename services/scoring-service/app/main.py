"""scoring-service — reconciliation, proper scoring, and the public track record.

Kept separate from prediction-service deliberately. This is the trust layer: if
the accuracy record lived alongside the model that produces the forecasts, a
change to model internals could alter the published history as a side effect.
Here it cannot.

Internal-only: not published to the host in docker-compose, reached solely by
core-api over the compose network.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import get_settings
from app.routers import scores
from app.services.storage import ScoringStore
from f1_common.health import build_health_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect(settings.mongo_uri, settings.mongo_database, settings.mongo_timeout_ms)

    try:
        await ScoringStore(db.db()).ensure_indexes()
    except Exception:
        logger.exception("index creation failed; continuing without it")

    yield
    db.close()


app = FastAPI(
    title="F1 Scoring Service",
    version="0.1.0",
    description="Post-race reconciliation, Brier/log scoring, calibration, track record.",
    lifespan=lifespan,
)

app.include_router(
    build_health_router(settings.service_name, settings.environment, db.client)
)
app.include_router(scores.router)
