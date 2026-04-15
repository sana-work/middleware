from typing import Optional, Dict, Any

from app.db.mongo import get_database
from app.core.logging import get_logger
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)


class SessionsRepository:
    """Repository for the sessions MongoDB collection."""

    @staticmethod
    async def create_or_update_session(
        session_id: str,
        soeid: str,
        correlation_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert a session document. Creates if missing, updates if exists."""
        db = get_database()
        now = format_iso(utc_now())

        await db.sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "soeid": soeid,
                    "updated_at": now,
                    "last_correlation_id": correlation_id,
                    "metadata": metadata,
                },
                "$setOnInsert": {
                    "session_id": session_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        logger.info("Session upserted", session_id=session_id, correlation_id=correlation_id)

    @staticmethod
    async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
        """Find a session by session_id."""
        db = get_database()
        doc = await db.sessions.find_one({"session_id": session_id}, {"_id": 0})
        return doc

    @staticmethod
    async def get_session_by_soeid(session_id: str, soeid: str) -> Optional[Dict[str, Any]]:
        """Find a session by session_id with soeid ownership check."""
        db = get_database()
        doc = await db.sessions.find_one(
            {"soeid": soeid, "session_id": session_id}, {"_id": 0}
        )
        return doc
