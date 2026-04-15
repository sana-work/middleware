from typing import Optional, Dict, Any, List

from pymongo.errors import DuplicateKeyError

from app.db.mongo import get_database
from app.core.logging import get_logger

logger = get_logger(__name__)


class EventsRepository:
    """Repository for the events MongoDB collection."""

    @staticmethod
    async def insert_event(doc: Dict[str, Any]) -> bool:
        """
        Insert an event document.
        Returns True if the insert succeeded, False if skipped due to idempotency.
        """
        db = get_database()
        try:
            await db.events.insert_one(doc)
            logger.info(
                "Event persisted",
                correlation_id=doc.get("correlation_id"),
                event_type=doc.get("event_type"),
                idempotency_key=doc.get("event_idempotency_key"),
            )
            return True
        except DuplicateKeyError:
            logger.warning(
                "Duplicate event skipped",
                idempotency_key=doc.get("event_idempotency_key"),
                correlation_id=doc.get("correlation_id"),
            )
            return False

    @staticmethod
    async def get_events_by_correlation(correlation_id: str) -> List[Dict[str, Any]]:
        """Return all events for a correlation_id, ordered by timestamp ascending."""
        db = get_database()
        cursor = (
            db.events.find({"correlation_id": correlation_id}, {"_id": 0})
            .sort("timestamp", 1)
        )
        return await cursor.to_list(length=1000)

    @staticmethod
    async def get_events_by_session(
        session_id: str,
        skip: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return events for a session, ordered by timestamp ascending, with pagination."""
        db = get_database()
        cursor = (
            db.events.find({"session_id": session_id}, {"_id": 0})
            .sort("timestamp", 1)
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)
