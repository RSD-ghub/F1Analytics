"""Mongo client construction.

Note this hands back a *new* client scoped to one database per call. There is
deliberately no shared, cross-service client object here — the previous Java
codebase had a single ``MongoProvider`` injected into repositories across four
unrelated domains, and reproducing that would defeat the service split.
"""

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


def create_client(uri: str, timeout_ms: int = 5000) -> AsyncIOMotorClient:
    """Build a Motor client with a bounded server-selection timeout.

    Motor's default is 30s, which turns an unreachable Mongo into a request that
    appears to hang. A short timeout surfaces the failure instead.
    """
    return AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        uuidRepresentation="standard",
    )


def get_database(client: AsyncIOMotorClient, name: str) -> AsyncIOMotorDatabase:
    return client[name]


async def ping(client: Optional[AsyncIOMotorClient]) -> bool:
    """Return whether Mongo currently answers. Never raises."""
    if client is None:
        return False
    try:
        await client.admin.command("ping")
        return True
    except Exception:
        return False
