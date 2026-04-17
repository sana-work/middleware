from typing import Optional, Dict, Any, List

from app.db.mongo import get_database
from app.core.logging import get_logger
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)


class ExecutionsRepository:
    """Repository for the recon MongoDB collection (Executions)."""

    @staticmethod
    def get_db_for_update():
        return get_database()

    @staticmethod
    async def create_execution(doc: Dict[str, Any]) -> None:
        """Insert a new execution document."""
        db = get_database()
        await db.recon.insert_one(doc)
        logger.info("Execution created", correlation_id=doc.get("correlation_id"))

    @staticmethod
    async def update_execution_status(
        correlation_id: str,
        status: str,
        latest_event_type: str,
        completed_at: Optional[str] = None,
    ) -> None:
        """Update the status of an execution."""
        db = get_database()
        now = format_iso(utc_now())
        update_fields: Dict[str, Any] = {
            "status": status,
            "latest_event_type": latest_event_type,
            "updated_at": now,
        }
        if completed_at:
            update_fields["completed_at"] = completed_at

        await db.recon.update_one(
            {"correlation_id": correlation_id},
            {"$set": update_fields},
        )
        logger.info(
            "Execution status updated",
            correlation_id=correlation_id,
            status=status,
            latest_event_type=latest_event_type,
        )

    @staticmethod
    async def get_execution(correlation_id: str) -> Optional[Dict[str, Any]]:
        """Find an execution by correlation_id."""
        db = get_database()
        doc = await db.recon.find_one({"correlation_id": correlation_id}, {"_id": 0})
        return doc

    @staticmethod
    async def get_execution_by_soeid(correlation_id: str, soeid: str) -> Optional[Dict[str, Any]]:
        """Find an execution by correlation_id with soeid ownership check."""
        db = get_database()
        doc = await db.recon.find_one(
            {"soeid": soeid, "correlation_id": correlation_id}, {"_id": 0}
        )
        return doc

    @staticmethod
    async def get_executions_by_session(
        session_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Find all executions for a session, with pagination."""
        db = get_database()
        cursor = (
            db.recon.find({"session_id": session_id}, {"_id": 0})
            .sort("created_at", 1)
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    @staticmethod
    async def count_executions_by_session(session_id: str) -> int:
        """Count total executions for a session."""
        db = get_database()
        return await db.recon.count_documents({"session_id": session_id})
