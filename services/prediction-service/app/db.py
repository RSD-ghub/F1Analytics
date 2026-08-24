"""Mongo wiring for prediction-service (database: f1_prediction)."""

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from f1_common.mongo import create_client, get_database

_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


def connect(uri: str, database: str, timeout_ms: int) -> AsyncIOMotorDatabase:
    global _client, _database
    _client = create_client(uri, timeout_ms)
    _database = get_database(_client, database)
    return _database


def close() -> None:
    global _client, _database
    if _client is not None:
        _client.close()
    _client = None
    _database = None


def client() -> Optional[AsyncIOMotorClient]:
    return _client


def db() -> AsyncIOMotorDatabase:
    if _database is None:
        raise RuntimeError("Mongo is not connected; connect() runs during app startup")
    return _database
