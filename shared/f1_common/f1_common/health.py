"""A consistent /health endpoint for every service.

Health reports dependency state but still returns 200 when a dependency is down.
The distinction matters for orchestration: a 503 tells Docker to restart the
container, which is the wrong response to "Mongo is briefly unreachable" — the
service itself is running correctly and should stay up and keep reporting the
problem rather than being killed.
"""

from typing import Callable, Optional

from fastapi import APIRouter
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

from f1_common.mongo import ping


class DependencyStatus(BaseModel):
    mongo: str  # "up" | "down"


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    service: str
    environment: str
    dependencies: DependencyStatus


def build_health_router(
    service_name: str,
    environment: str,
    get_mongo_client: Callable[[], Optional[AsyncIOMotorClient]],
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        mongo_up = await ping(get_mongo_client())
        return HealthResponse(
            status="ok" if mongo_up else "degraded",
            service=service_name,
            environment=environment,
            dependencies=DependencyStatus(mongo="up" if mongo_up else "down"),
        )

    return router
