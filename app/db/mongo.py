from __future__ import annotations
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_database() -> AsyncIOMotorDatabase:
    """Return the MongoDB database instance."""
    assert _db is not None, "MongoDB not initialized. Call init_mongo() first."
    return _db


async def init_mongo() -> None:
    """Initialize the MongoDB client and create required indexes."""
    global _client, _db
    _client = AsyncIOMotorClient(settings.MONGODB_URI)
    _db = _client[settings.MONGODB_DATABASE]
    logger.info("MongoDB client connected", database=settings.MONGODB_DATABASE)
    await _create_indexes()


async def close_mongo() -> None:
    """Close the MongoDB client."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB client closed")


async def _create_indexes() -> None:
    """Create all required MongoDB indexes as defined in the spec."""
    db = get_database()

    # sessions indexes
    await db.sessions.create_index("session_id", unique=True)
    await db.sessions.create_index([("soeid", pymongo.ASCENDING), ("session_id", pymongo.ASCENDING)])

    # executions indexes
    await db.executions.create_index("correlation_id", unique=True)
    await db.executions.create_index("session_id")
    await db.executions.create_index([("soeid", pymongo.ASCENDING), ("correlation_id", pymongo.ASCENDING)])

    # events indexes
    await db.events.create_index("correlation_id")
    await db.events.create_index("session_id")
    await db.events.create_index("timestamp")
    await db.events.create_index(
        [("session_id", pymongo.ASCENDING), ("timestamp", pymongo.ASCENDING)]
    )
    await db.events.create_index([("soeid", pymongo.ASCENDING), ("correlation_id", pymongo.ASCENDING)])
    await db.events.create_index("event_idempotency_key", unique=True)

    logger.info("MongoDB indexes created")
