"""ingestion-service — owns all FastF1-derived data and its completeness guarantees.

Internal-only: this service is not published to the host in docker-compose and is
reached solely by core-api over the compose network.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.config import get_settings
from app.dependencies import get_runner
from app.routers import data, forward, ingest
from app.services import scheduler
from app.services.storage import IngestionStore
from f1_common.health import build_health_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect(settings.mongo_uri, settings.mongo_database, settings.mongo_timeout_ms)

    # Index creation is best-effort: Mongo being briefly unreachable at boot
    # should degrade query speed, not prevent the service from starting and
    # reporting itself as degraded on /health.
    try:
        await IngestionStore(db.db()).ensure_indexes()
    except Exception:
        logger.exception("index creation failed; continuing without it")

    scheduler.start(
        get_runner(),
        settings.auto_refresh_hours,
        settings.grid_check_minutes,
    )
    yield
    scheduler.shutdown()
    db.close()


app = FastAPI(
    title="F1 Ingestion Service",
    version="0.1.0",
    description="FastF1 acquisition, completeness tracking, and point-in-time snapshots.",
    lifespan=lifespan,
)

app.include_router(
    build_health_router(settings.service_name, settings.environment, db.client)
)
app.include_router(ingest.router)
app.include_router(forward.router)
app.include_router(data.router)
