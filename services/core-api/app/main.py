"""core-api — the only publicly reachable service.

Everything the browser touches comes through here. The internal services are
not published to the host, so this is the single place authentication is
enforced and the single place a downstream outage has to be turned into
something a page can render.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings
from app.routers import auth, bernie, blog, dashboard
from app.services.downstream import DownstreamUnavailable
from app.services.security import AuthConfigurationError, require_secret
from app.services.conversation import ConversationStore
from app.services.users import UserStore
from f1_common.health import build_health_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.connect(settings.mongo_uri, settings.mongo_database, settings.mongo_timeout_ms)

    try:
        await UserStore(db.db()).ensure_indexes()
        await ConversationStore(db.db()).ensure_indexes()
    except Exception:
        logger.exception("index creation failed; email uniqueness is NOT enforced")

    # Surface a missing or weak signing key at startup rather than on the first
    # login attempt. It is a deployment error, and the earliest possible loud
    # failure is the cheapest one.
    try:
        require_secret(settings.jwt_secret)
    except AuthConfigurationError as exc:
        logger.error("AUTH DISABLED: %s", exc)

    yield
    db.close()


app = FastAPI(
    title="F1 Core API",
    version="0.1.0",
    description="Auth, aggregation, and the Bernie strategist layer.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DownstreamUnavailable)
async def downstream_unavailable(_: Request, exc: DownstreamUnavailable):
    """503, not 500.

    An internal service being unreachable is not core-api failing, and reporting
    it as a server error sends whoever is on call to the wrong place.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc), "service": exc.service},
    )


app.include_router(
    build_health_router(settings.service_name, settings.environment, db.client)
)
app.include_router(auth.router)
app.include_router(blog.router)
app.include_router(bernie.router)
app.include_router(dashboard.router)
