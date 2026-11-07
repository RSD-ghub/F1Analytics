"""prediction-service — point-in-time features, locked forecasts, championship odds.

Internal-only: not published to the host in docker-compose, reached solely by
core-api over the compose network.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import get_settings
from app.dependencies import get_client, get_predictor
from app.routers import circuits as circuits_router
from app.routers import predictions
from app.services import scheduler as lock_scheduler
from app.services.model import MODEL_STATE_DIR, ensure_state_dir
from app.services.scheduler import LockScheduler
from app.services.storage import PredictionStore
from f1_common import cache
from f1_common.health import build_health_router
from f1_common.observability import RequestIdMiddleware, configure_logging

settings = get_settings()
configure_logging(settings.log_level, settings.log_format, settings.service_name)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Before anything loads the model. On a deployment MODEL_STATE_DIR points at
    # a mounted volume, which is empty the first time it is created — the
    # packaged artifact is copied across and becomes the starting champion.
    # Every promotion after that writes to the volume and survives a rebuild.
    seeded = ensure_state_dir()
    if seeded:
        logger.info(
            "seeded %s from the packaged artifact: %s",
            MODEL_STATE_DIR, ", ".join(seeded),
        )

    db.connect(settings.mongo_uri, settings.mongo_database, settings.mongo_timeout_ms)
    cache.configure(settings.redis_url)
    # A restart may have loaded a newly promoted model or archetype
    # artifact, so whatever core-api remembers from this service is retired.
    await cache.bump("prediction")

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

    lock_scheduler.start(
        LockScheduler(
            predictor=get_predictor(),
            client=get_client(),
            pre_quali_hours=settings.pre_quali_lock_hours_before,
            post_quali_hours=settings.post_quali_lock_hours_before,
            final_grid_minutes=settings.final_grid_lock_minutes_before,
            sprint_hours=settings.sprint_lock_hours_before,
        ),
        settings.lock_check_minutes,
    )

    yield
    lock_scheduler.shutdown()
    await cache.close()
    db.close()


app = FastAPI(
    title="F1 Prediction Service",
    version="0.1.0",
    description="Point-in-time features, calibrated forecasts, championship simulation.",
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware, service=settings.service_name)

app.include_router(
    build_health_router(settings.service_name, settings.environment, db.client)
)
app.include_router(predictions.router)
app.include_router(predictions.championship_router)
app.include_router(circuits_router.router)
